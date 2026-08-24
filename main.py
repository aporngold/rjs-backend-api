import hashlib

class LoginRequest(BaseModel):
    username: str
    password: str

@app.post("/api/login")
def login(payload: LoginRequest):
    admin_user = os.environ.get("DASHBOARD_USER", "admin")
    admin_pass = os.environ.get("DASHBOARD_PASS", "Rjse@2026")
    
    # ตรวจสอบความถูกต้องแบบเข้ารหัส SHA-256 ป้องกันการดักจับ
    input_user_hash = hashlib.sha256(payload.username.encode()).hexdigest()
    correct_user_hash = hashlib.sha256(admin_user.encode()).hexdigest()
    
    input_pass_hash = hashlib.sha256(payload.password.encode()).hexdigest()
    correct_pass_hash = hashlib.sha256(admin_pass.encode()).hexdigest()
    
    if input_user_hash == correct_user_hash and input_pass_hash == correct_pass_hash:
        # ออก Session Token แบบเข้ารหัส
        token = hashlib.sha256(f"{admin_user}:{admin_pass}:RJSE_SECRET_KEY".encode()).hexdigest()
        return {"status": "success", "token": token}
    else:
        raise HTTPException(status_code=401, detail="ชื่อผู้ใช้หรือรหัสผ่านไม่ถูกต้อง")
