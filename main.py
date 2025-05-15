from flask import Flask, request, jsonify
from flask_cors import CORS
from flasgger import Swagger, swag_from
from bson.objectid import ObjectId
import pandas as pd
import logging
import re
import requests
from pymongo import MongoClient
from google import genai
from google.genai import types

# ======== CONFIG ========
VERIFY_TOKEN = "" # <-- xác thực với facebook
PAGE_ACCESS_TOKEN = ""  # <-- Dán token trang của bạn ở đây
GENAI_API_KEY = ""  # <-- API key Gemini

# ======== INIT APP ========
app = Flask(__name__)
CORS(app)
Swagger(app)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ======== MongoDB ========
mongo_client = MongoClient("mongodb://localhost:27017/")
db = mongo_client["qa_database"]
qa_collection = db["custom_qa"]

# ======== Gemini Config ========
genai_client = genai.Client(api_key=GENAI_API_KEY)
model = "gemini-2.5-flash-preview-04-17"
generate_config = types.GenerateContentConfig(response_mime_type="text/plain")

# ======== Helper ========
def normalize_question(text):
    text = text.strip().lower()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+\?', '?', text)
    return text

def get_bot_response(question):
    """Gửi câu hỏi tới hệ thống và lấy phản hồi (dùng nội bộ Messenger)"""
    try:
        response = requests.post("http://localhost:5000/ask", json={"question": question}, timeout=5)
        return response.json().get("answer", "Xin lỗi, tôi không hiểu.")
    except Exception as e:
        return "Lỗi hệ thống: " + str(e)

def send_message(recipient_id, message_text):
    """Gửi tin nhắn về Messenger"""
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    params = {"access_token": PAGE_ACCESS_TOKEN}
    headers = {"Content-Type": "application/json"}
    requests.post("https://graph.facebook.com/v16.0/me/messages", params=params, headers=headers, json=payload)

# ======== Messenger Webhook ========
@app.route("/webhook", methods=["GET", "POST"])
def messenger_webhook():
    if request.method == "POST":
        data = request.get_json()
        print("Dữ liệu webhook nhận được:", data)

        for entry in data.get("entry", []):
            for messaging_event in entry.get("messaging", []):
                sender_id = messaging_event["sender"]["id"]
                if "message" in messaging_event:
                    message_text = messaging_event["message"].get("text")
                    if message_text:
                        print(f"[Webhook] Nhận từ {sender_id}: {message_text}")
                        answer = get_bot_response(message_text)
                        print(f"[Bot] Trả lời: {answer}")
                        send_message(sender_id, answer)

        return "ok", 200

# ======== API: Ask ========
@app.route("/ask", methods=["POST"])
@swag_from({
    'tags': ['Q&A'],
    'parameters': [{
        "name": "question",
        "in": "body",
        "schema": {"type": "object", "properties": {
            "question": {"type": "string", "example": "ai tạo ra bạn"}
        }, "required": ["question"]}
    }],
    'responses': {
        200: {"description": "Trả lời thành công"},
        400: {"description": "Thiếu dữ liệu"},
        500: {"description": "Lỗi hệ thống"}
    }
})
def ask_question():
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": "Thiếu 'question'"}), 400

    original = data["question"]
    normalized = normalize_question(original)

    record = qa_collection.find_one({"normalized_question": normalized})
    if record:
        return jsonify({"answer": record["answer"]})

    try:
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=original)])]
        response_text = ""
        for chunk in genai_client.models.generate_content_stream(model=model, contents=contents, config=generate_config):
            response_text += chunk.text
        return jsonify({"answer": response_text})
    except Exception as e:
        logger.error(f"Gemini error: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ======== API: Add Q&A ========
@app.route("/add_qa", methods=["POST"])
@swag_from({
    'tags': ['Q&A'],
    'parameters': [{
        "name": "body",
        "in": "body",
        "schema": {"type": "object", "properties": {
            "question": {"type": "string"},
            "answer": {"type": "string"}
        }, "required": ["question", "answer"]}
    }],
    'responses': {200: {"description": "Thêm thành công"}}
})
def add_qa():
    data = request.get_json()
    if not data or "question" not in data or "answer" not in data:
        return jsonify({"error": "Thiếu 'question' hoặc 'answer'"}), 400

    normalized = normalize_question(data["question"])
    if qa_collection.find_one({"normalized_question": normalized}):
        return jsonify({"message": "Câu hỏi đã tồn tại"}), 200

    result = qa_collection.insert_one({
        "original_question": data["question"],
        "normalized_question": normalized,
        "answer": data["answer"]
    })

    return jsonify({"message": "Thêm thành công", "id": str(result.inserted_id)})

# ======== API: Update Q&A ========
@app.route("/update_qa", methods=["PUT"])
@swag_from({
    'tags': ['Q&A'],
    'parameters': [{
        "name": "body",
        "in": "body",
        "schema": {
            "type": "object",
            "properties": {
                "id": {"type": "string"},
                "new_question": {"type": "string"},
                "new_answer": {"type": "string"}
            },
            "required": ["id"]
        }
    }],
    'responses': {
        200: {"description": "Cập nhật thành công"},
        404: {"description": "Không tìm thấy ID"}
    }
})
def update_qa():
    data = request.get_json()
    if "id" not in data:
        return jsonify({"error": "Thiếu ID"}), 400

    try:
        _id = ObjectId(data["id"])
    except:
        return jsonify({"error": "ID không hợp lệ"}), 400

    update = {}
    if "new_question" in data:
        update["original_question"] = data["new_question"]
        update["normalized_question"] = normalize_question(data["new_question"])
    if "new_answer" in data:
        update["answer"] = data["new_answer"]

    if not update:
        return jsonify({"error": "Không có gì để cập nhật"}), 400

    result = qa_collection.update_one({"_id": _id}, {"$set": update})
    if result.matched_count == 0:
        return jsonify({"error": "Không tìm thấy ID"}), 404

    return jsonify({"message": "Cập nhật thành công"})

# ======== API: Import Excel ========
@app.route("/import_qa", methods=["POST"])
@swag_from({
    'tags': ['Q&A'],
    'consumes': ["multipart/form-data"],
    'parameters': [{
        "name": "file",
        "in": "formData",
        "type": "file",
        "required": True
    }],
    'responses': {200: {"description": "Import thành công"}}
})
def import_qa():
    if "file" not in request.files:
        return jsonify({"error": "Không tìm thấy file"}), 400

    try:
        df = pd.read_excel(request.files["file"])
        df.columns = [str(c).strip().lower() for c in df.columns]

        col_map = {}
        for c in df.columns:
            if c in ["question", "câu hỏi"]:
                col_map["question"] = c
            elif c in ["answer", "trả lời"]:
                col_map["answer"] = c

        if "question" not in col_map or "answer" not in col_map:
            return jsonify({"error": "File phải có cột 'question' và 'answer' (hoặc 'câu hỏi' và 'trả lời')"}), 400

        inserted, skipped = 0, 0
        for _, row in df.iterrows():
            q = str(row[col_map["question"]]).strip()
            a = str(row[col_map["answer"]]).strip()
            if not q or not a:
                continue

            normalized = normalize_question(q)
            if qa_collection.find_one({"normalized_question": normalized}):
                skipped += 1
                continue

            qa_collection.insert_one({
                "original_question": q,
                "normalized_question": normalized,
                "answer": a
            })
            inserted += 1

        return jsonify({"message": "Import thành công", "inserted": inserted, "skipped": skipped})

    except Exception as e:
        logger.error(f"Import lỗi: {str(e)}")
        return jsonify({"error": str(e)}), 500

# ======== Start App ========
if __name__ == "__main__":
    app.run(debug=True)
