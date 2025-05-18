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

# ======== INIT APP ========
app = Flask(__name__)
CORS(app)
Swagger(app)

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ======== MongoDB ========
mongo_client = MongoClient("mongodb+srv://nguyenhautq2k4:aSIAzthfbxdSjI9k@dbmess.quy0clt.mongodb.net/?retryWrites=true&w=majority&tls=true&tlsAllowInvalidCertificates=true")



db = mongo_client["qa_database"]
qa_collection = db["custom_qa"]

# ======== Load Config from DB ========
def load_config():
    config = db["config"].find_one({"_id": "default"})
    if config:
        return config
    else:
        # Khởi tạo giá trị mặc định nếu chưa có
        default_config = {
            "verify_token": "Hau204",
            "page_access_token": "",
            "genai_api_key": ""
        }
        db["config"].insert_one({"_id": "default", **default_config})
        return default_config

try:
    config = load_config()
    VERIFY_TOKEN = config["verify_token"]
    PAGE_ACCESS_TOKEN = config["page_access_token"]
    GENAI_API_KEY = config["genai_api_key"]
    genai_client = genai.Client(api_key=GENAI_API_KEY) if GENAI_API_KEY else None
except Exception as e:
    logger.error("❌ Không thể kết nối MongoDB hoặc load config:", exc_info=e)
    config = {}
    VERIFY_TOKEN = "invalid"
    PAGE_ACCESS_TOKEN = ""
    GENAI_API_KEY = ""
    genai_client = None

VERIFY_TOKEN = config["verify_token"]
PAGE_ACCESS_TOKEN = config["page_access_token"]
GENAI_API_KEY = config["genai_api_key"]

# ======== Gemini Config ========
genai_client = genai.Client(api_key=GENAI_API_KEY) if GENAI_API_KEY else None
model = "gemini-2.5-flash-preview-04-17"
generate_config = types.GenerateContentConfig(response_mime_type="text/plain")

# ======== Helper ========
def normalize_question(text):
    text = text.strip().lower()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+\?', '?', text)
    return text

def get_bot_response(question):
    if not genai_client:
        return "Lỗi: Chưa cấu hình API key cho Gemini. Vui lòng vào /config để cập nhật."
    try:
        response = requests.post("http://localhost:5000/ask", json={"question": question}, timeout=20)
        if response.status_code != 200:
            return f"Lỗi hệ thống: /ask trả về {response.status_code}"
        return response.json().get("answer", "Xin lỗi, tôi không hiểu.")
    except requests.exceptions.Timeout:
        return "⏱️ Câu hỏi mất quá nhiều thời gian để xử lý. Vui lòng thử lại sau."
    except Exception as e:
        return "Lỗi hệ thống: " + str(e)
    except Exception as e:
        return "Lỗi hệ thống: " + str(e)

def send_message(recipient_id, message_text):
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message_text}
    }
    params = {"access_token": PAGE_ACCESS_TOKEN}
    headers = {"Content-Type": "application/json"}

    res = requests.post(
        "https://graph.facebook.com/v18.0/me/messages",
        params=params,
        headers=headers,
        json=payload
    )
    print(f"[Facebook] Status: {res.status_code}", res.text)

# ======== Config API ========
@app.route("/config", methods=["GET"])
@swag_from({
    'tags': ['Config'],
    'responses': {
        200: {
            "description": "Lấy cấu hình hiện tại",
            "schema": {
                "type": "object",
                "properties": {
                    "verify_token": {"type": "string"},
                    "page_access_token": {"type": "string"},
                    "genai_api_key": {"type": "string"}
                }
            }
        }
    }
})
def get_config():
    config = db["config"].find_one({"_id": "default"})
    if config:
        return jsonify({
            "verify_token": config.get("verify_token"),
            "page_access_token": config.get("page_access_token"),
            "genai_api_key": config.get("genai_api_key")
        })
    return jsonify({"error": "Không tìm thấy cấu hình"}), 404

@app.route("/config", methods=["PUT"])
@swag_from({
    'tags': ['Config'],
    'parameters': [
        {
            "name": "body",
            "in": "body",
            "required": True,
            "schema": {
                "type": "object",
                "properties": {
                    "verify_token": {"type": "string"},
                    "page_access_token": {"type": "string"},
                    "genai_api_key": {"type": "string"}
                }
            }
        }
    ],
    'responses': {
        200: {"description": "Cập nhật thành công"},
        400: {"description": "Thiếu dữ liệu"}
    }
})
def update_config():
    global VERIFY_TOKEN, PAGE_ACCESS_TOKEN, GENAI_API_KEY, genai_client

    data = request.get_json()
    update = {}
    if "verify_token" in data:
        update["verify_token"] = data["verify_token"]
    if "page_access_token" in data:
        update["page_access_token"] = data["page_access_token"]
    if "genai_api_key" in data:
        update["genai_api_key"] = data["genai_api_key"]

    if not update:
        return jsonify({"error": "Không có dữ liệu để cập nhật"}), 400

    db["config"].update_one({"_id": "default"}, {"$set": update}, upsert=True)

    # Reload config variables immediately
    new_config = load_config()
    VERIFY_TOKEN = new_config["verify_token"]
    PAGE_ACCESS_TOKEN = new_config["page_access_token"]
    GENAI_API_KEY = new_config["genai_api_key"]
    genai_client = genai.Client(api_key=GENAI_API_KEY)

    return jsonify({"message": "Đã cập nhật cấu hình"})

@app.route("/ask", methods=["POST"])
def ask_question():
    data = request.get_json()
    if not data or "question" not in data:
        return jsonify({"error": "Thiếu 'question'"}), 400

    original = data["question"]
    normalized = normalize_question(original)
    record = qa_collection.find_one({"normalized_question": normalized})
    if record:
        return jsonify({"answer": record["answer"]})

    if not genai_client:
        return jsonify({"error": "Chưa cấu hình Gemini API key"}), 400

    try:
        contents = [types.Content(role="user", parts=[types.Part.from_text(text=original)])]
        response_text = ""
        for chunk in genai_client.models.generate_content_stream(model=model, contents=contents, config=generate_config):
            response_text += chunk.text
        return jsonify({"answer": response_text})
    except Exception as e:
        logger.error(f"Gemini error: {str(e)}")
        return jsonify({"error": str(e)}), 500

#  thêm cau hỏi mới
@swag_from({
    'tags': ['QA'],
    'parameters': [
        {
            "name": "body",
            "in": "body",
            "required": True,
            "schema": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "answer": {"type": "string"}
                }
            }
        }
    ],
    'responses': {
        200: {"description": "Thêm câu hỏi thành công hoặc đã tồn tại"},
        400: {"description": "Thiếu dữ liệu"}
    }
})

@app.route("/add_qa", methods=["POST"])
def add_qa():
    data = request.get_json()
    if not data or "question" not in data or "answer" not in data:
        return jsonify({"error": "Thiếu 'question' hoặc 'answer'"}), 400

    normalized = normalize_question(data["question"])
    if qa_collection.find_one({"normalized_question": normalized}):
        return jsonify({"message": "Câu hỏi đã tồn tại"}), 200

    qa_collection.insert_one({
        "original_question": data["question"],
        "normalized_question": normalized,
        "answer": data["answer"]
    })
    return jsonify({"message": "Đã thêm câu hỏi thành công"})
#  update câu hỏi
@swag_from({
    'tags': ['QA'],
    'parameters': [
        {
            "name": "body",
            "in": "body",
            "required": True,
            "schema": {
                "type": "object",
                "properties": {
                    "id": {"type": "string"},
                    "new_question": {"type": "string"},
                    "new_answer": {"type": "string"}
                }
            }
        }
    ],
    'responses': {
        200: {"description": "Cập nhật thành công"},
        400: {"description": "Thiếu ID hoặc không có gì để cập nhật"},
        404: {"description": "Không tìm thấy ID"}
    }
})

@app.route("/update_qa", methods=["PUT"])
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

# import câu hỏi từ file excel
@swag_from({
    'tags': ['QA'],
    'consumes': ["multipart/form-data"],
    'parameters': [
        {
            "name": "file",
            "in": "formData",
            "type": "file",
            "required": True,
            "description": "File Excel chứa cột 'question' và 'answer' (hoặc 'câu hỏi' và 'trả lời')"
        }
    ],
    'responses': {
        200: {"description": "Import thành công"},
        400: {"description": "Lỗi định dạng file hoặc thiếu cột"},
        500: {"description": "Lỗi hệ thống khi xử lý"}
    }
})

@app.route("/import_qa", methods=["POST"])
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
            return jsonify({"error": "File phải có cột 'question' và 'answer' hoặc 'câu hỏi' và 'trả lời'"}), 400

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

# ======== Messenger Webhook ========
@app.route("/webhook", methods=["GET", "POST"])
def messenger_webhook():
    if request.method == "GET":
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        mode = request.args.get("hub.mode")
        if mode == "subscribe" and token == VERIFY_TOKEN:
            return challenge, 200
        else:
            return "Verification token mismatch", 403

    if request.method == "POST":
        try:
            data = request.get_json(force=True)
            print("\n📩 Dữ liệu POST nhận được từ Facebook:")
            print(data)

            for entry in data.get("entry", []):
                for messaging_event in entry.get("messaging", []):
                    sender_id = messaging_event.get("sender", {}).get("id")
                    message_text = messaging_event.get("message", {}).get("text")

                    if sender_id and message_text:
                        print(f"✅ Gửi từ: {sender_id} - Nội dung: {message_text}")
                        answer = get_bot_response(message_text)
                        print(f"🤖 Bot trả lời: {answer}")
                        send_message(sender_id, answer)
                    else:
                        print("⚠️ Không tìm thấy sender_id hoặc message_text")

            return "ok", 200

        except Exception as e:
            import traceback
            traceback.print_exc()
            return "Internal Server Error", 500

# ======== Start App ========
if __name__ == "__main__":
    app.run(debug=True)
