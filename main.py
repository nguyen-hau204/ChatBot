from flask import Flask, request, jsonify
from flask_cors import CORS
from flasgger import Swagger, swag_from
from bson.objectid import ObjectId
from werkzeug.security import generate_password_hash, check_password_hash
from pymongo import MongoClient
from functools import wraps
from datetime import datetime, timedelta
import pandas as pd
import logging
import re
import jwt


# ======== INIT APP ========
app = Flask(__name__)
CORS(app)
Swagger(app)
app.config['SECRET_KEY'] = 'Hau2004'

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

# ======== MongoDB ========
mongo_client = MongoClient(
    "mongodb+srv://nguyenhautq2k4:aSIAzthfbxdSjI9k@dbmess.quy0clt.mongodb.net/?retryWrites=true&w=majority&tls=true&tlsAllowInvalidCertificates=true"
)
db = mongo_client["qa_database"]
qa_collection = db["custom_qa"]

# ======== Role Decorator ========
def admin_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        data = request.get_json()
        username = data.get("username")
        user = db["users"].find_one({"username": username})
        if not user or user.get("role") != "admin":
            return jsonify({"error": "Chỉ admin mới được phép truy cập"}), 403
        return f(*args, **kwargs)
    return decorated_function

# ======== Helpers ========
def normalize_question(text):
    text = text.strip().lower()
    text = re.sub(r'\s+', ' ', text)
    text = re.sub(r'\s+\?', '?', text)
    return text


# ======== JWT Decorators ========
def token_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        token = None
        if 'Authorization' in request.headers:
            bearer = request.headers['Authorization']
            if bearer.startswith('Bearer '):
                token = bearer[7:]

        if not token:
            return jsonify({'error': 'Token không được cung cấp'}), 401

        try:
            data = jwt.decode(token, app.config['SECRET_KEY'], algorithms=["HS256"])
            request.user = data
        except jwt.ExpiredSignatureError:
            return jsonify({'error': 'Token đã hết hạn'}), 401
        except jwt.InvalidTokenError:
            return jsonify({'error': 'Token không hợp lệ'}), 401

        return f(*args, **kwargs)
    return decorated

# ======== Auth Routes ========

# ======== Auth Routes ========
@app.route("/register", methods=["POST"])
@swag_from({
    'tags': ['Auth'],
    'parameters': [{
        'name': 'body', 'in': 'body', 'required': True,
        'schema': {
            'type': 'object',
            'properties': {
                'username': {'type': 'string'},
                'password': {'type': 'string'},
                'role': {'type': 'string', 'default': 'user'}
            },
            'required': ['username', 'password']
        }
    }],
    'responses': {
        200: {'description': 'Tạo tài khoản thành công'},
        400: {'description': 'Tài khoản đã tồn tại hoặc thiếu trường'}
    }
})
def register():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")
    role = data.get("role", "user")

    if not username or not password:
        return jsonify({"error": "Thiếu username hoặc password"}), 400

    if db["users"].find_one({"username": username}):
        return jsonify({"error": "Tài khoản đã tồn tại"}), 400

    hashed_password = generate_password_hash(password)
    db["users"].insert_one({
        "username": username,
        "password": hashed_password,
        "role": role,
        "created_at": datetime.utcnow()
    })

    return jsonify({"message": "Tạo tài khoản thành công", "role": role})

@app.route("/login", methods=["POST"])
@swag_from({
    'tags': ['Auth'],
    'parameters': [{
        'name': 'body', 'in': 'body', 'required': True,
        'schema': {
            'type': 'object',
            'properties': {
                'username': {'type': 'string'},
                'password': {'type': 'string'}
            },
            'required': ['username', 'password']
        }
    }],
    'responses': {
        200: {'description': 'Đăng nhập thành công'},
        401: {'description': 'Sai thông tin đăng nhập'}
    }
})
def login():
    data = request.get_json()
    username = data.get("username")
    password = data.get("password")

    user = db["users"].find_one({"username": username})
    if not user or not check_password_hash(user["password"], password):
        return jsonify({"error": "Sai username hoặc password"}), 401

    token = jwt.encode({
        "username": username,
        "role": user.get("role", "user"),
        "exp": datetime.utcnow() + timedelta(hours=2)
    }, app.config['SECRET_KEY'], algorithm="HS256")

    return jsonify({"message": "Đăng nhập thành công", "token": token})


# ======== QA Routes ========

@app.route("/add_qa", methods=["POST"])
@swag_from({
    'tags': ['QA'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'question': {'type': 'string'},
                    'answer': {'type': 'string'}
                },
                'required': ['question', 'answer']
            }
        }
    ],
    'responses': {
        200: {'description': 'Thành công'},
        400: {'description': 'Dữ liệu không hợp lệ'},
        401: {'description': 'Chưa đăng nhập'},
        403: {'description': 'Không có quyền truy cập'}
    }
})
@token_required
def add_qa():
    data = request.get_json()
    question = data.get("question")
    answer = data.get("answer")
    if not question or not answer:
        return jsonify({"error": "Thiếu dữ liệu"}), 400

    normalized = normalize_question(question)
    if qa_collection.find_one({"normalized_question": normalized}):
        return jsonify({"message": "Câu hỏi đã tồn tại"}), 200

    qa_collection.insert_one({
        "original_question": question,
        "normalized_question": normalized,
        "answer": answer
    })
    return jsonify({"message": "Đã thêm câu hỏi thành công"})

@app.route("/update_qa", methods=["PUT"])
@swag_from({
    'tags': ['QA'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'id': {'type': 'string'},
                    'new_question': {'type': 'string'},
                    'new_answer': {'type': 'string'}
                },
                'required': ['id']
            }
        }
    ],
    'responses': {
        200: {'description': 'Cập nhật thành công'},
        400: {'description': 'Dữ liệu không hợp lệ'},
        403: {'description': 'Không có quyền truy cập'},
        404: {'description': 'Không tìm thấy ID'}
    }
})
@admin_required
def update_qa():
    data = request.get_json()
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

@app.route("/import_qa", methods=["POST"])
@swag_from({
    'tags': ['QA'],
    'consumes': ['multipart/form-data'],
    'parameters': [
        {
            'name': 'file',
            'in': 'formData',
            'type': 'file',
            'required': True,
            'description': 'Tệp Excel có cột question và answer'
        }
    ],
    'responses': {
        200: {'description': 'Import thành công'},
        400: {'description': 'File không hợp lệ'},
        403: {'description': 'Không có quyền truy cập'},
        500: {'description': 'Lỗi hệ thống'}
    }
})
@admin_required
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
            return jsonify({"error": "Thiếu cột 'question' và 'answer'"}), 400

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

@app.route("/ask", methods=["POST"])
@swag_from({
    'tags': ['QA'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'question': {'type': 'string'}
                },
                'required': ['question']
            }
        }
    ],
    'responses': {
        200: {'description': 'Trả lời câu hỏi'},
        400: {'description': 'Thiếu câu hỏi'}
    }
})
def ask_question():
    data = request.get_json()
    question = data.get("question")
    if not question:
        return jsonify({"error": "Thiếu câu hỏi"}), 400

    normalized = normalize_question(question)
    record = qa_collection.find_one({"normalized_question": normalized})
    if record:
        return jsonify({"answer": record["answer"]})

    return jsonify({"answer": "Xin lỗi, tôi không tìm thấy câu trả lời."})

@app.route("/config", methods=["GET"])
@swag_from({
    'tags': ['QA'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'question': {'type': 'string'},
                    'answer': {'type': 'string'},
                    'username': {'type': 'string'}
                },
                'required': ['question', 'answer', 'username']
            }
        }
    ],
    'responses': {
        200: {'description': 'Thành công'},
        400: {'description': 'Dữ liệu không hợp lệ'},
        403: {'description': 'Không có quyền truy cập'},
        404: {'description': 'Không tìm thấy'}
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
    'tags': ['QA'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'question': {'type': 'string'},
                    'answer': {'type': 'string'},
                    'username': {'type': 'string'}
                },
                'required': ['question', 'answer', 'username']
            }
        }
    ],
    'responses': {
        200: {'description': 'Thành công'},
        400: {'description': 'Dữ liệu không hợp lệ'},
        403: {'description': 'Không có quyền truy cập'},
        404: {'description': 'Không tìm thấy'}
    }
})
def update_config():
    data = request.get_json()
    update = {}
    if "verify_token" in data:
        update["verify_token"] = data["verify_token"]
    if "page_access_token" in data:
        update["page_access_token"] = data["page_access_token"]
    if "genai_api_key" in data:
        update["genai_api_key"] = data["genai_api_key"]
    if not update:
        return jsonify({"error": "Không có gì để cập nhật"}), 400
    db["config"].update_one({"_id": "default"}, {"$set": update}, upsert=True)
    return jsonify({"message": "Đã cập nhật cấu hình"})

# ======== Run App ========
if __name__ == '__main__':
    app.run(host="0.0.0.0", port=8000)

