import os
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client

app = FastAPI(title="RJSE Secure API (Production)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")
LINE_CHANNEL_ACCESS_TOKEN = os.environ.get("LINE_CHANNEL_ACCESS_TOKEN")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

def send_line_broadcast(message: str):
    """ส่งข้อความแจ้งเตือนผ่าน LINE Messaging API"""
    if not LINE_CHANNEL_ACCESS_TOKEN:
        return
    try:
        url = "https://api.line.me/v2/bot/message/broadcast"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {LINE_CHANNEL_ACCESS_TOKEN}"
        }
        payload = {
            "messages": [
                {
                    "type": "text",
                    "text": message
                }
            ]
        }
        res = requests.post(url, headers=headers, json=payload, timeout=5)
        print(f"LINE API Response: {res.status_code}")
    except Exception as e:
        print(f"LINE Messaging API Error: {e}")

class StatusUpdate(BaseModel):
    opportunity_id: str
    new_status: str
    project_name: Optional[str] = ""

class NoteUpdate(BaseModel):
    opportunity_id: str
    notes: str

class ManualLeadCreate(BaseModel):
    project_name: str
    project_type: str
    total_budget: float
    contractor_name: str
    contractor_phone: str
    contractor_email: Optional[str] = ""

@app.get("/")
def root():
    return {"status": "online", "message": "RJSE Backend API Production is running"}

@app.get("/api/projects")
def get_all_projects():
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        res = supabase.table("projects").select("""
            id,
            project_code,
            project_name,
            project_type,
            total_budget,
            contractor_name,
            contractor_phone,
            contractor_email,
            opportunities (
                id,
                opportunity_score,
                score_tier,
                recommended_products,
                lead_status,
                score_reasons
            )
        """).order("created_at", desc=True).execute()
        return {"status": "success", "data": res.data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-status")
def update_lead_status(payload: StatusUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        supabase.table("opportunities").update({
            "lead_status": payload.new_status
        }).eq("id", payload.opportunity_id).execute()

        if payload.new_status in ["ลูกค้าสนใจ", "นัดเข้าพบ"]:
            alert_msg = f"🎯 [อัปเดตงานขายจริง]: {payload.new_status}\nโครงการ: {payload.project_name or '-'}\nกรุณาตรวจสอบรายละเอียดในระบบ RJSE Dashboard"
            send_line_broadcast(alert_msg)

        return {"status": "success", "message": "Status updated successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/update-note")
def update_lead_note(payload: NoteUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        supabase.table("opportunities").update({
            "score_reasons": {"sales_note": payload.notes}
        }).eq("id", payload.opportunity_id).execute()
        return {"status": "success", "message": "Note saved successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/create-lead")
def create_manual_lead(payload: ManualLeadCreate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    try:
        score = 60
        if payload.total_budget >= 10000000:
            score += 25
        elif payload.total_budget >= 3000000:
            score += 15
        
        is_ind = "โรงงาน" in payload.project_type or "อุตสาหกรรม" in payload.project_type
        if is_ind:
            score += 10
            products = "หม้อแปลงไฟฟ้า, ตู้ MDB, ท่อหนา RSC/IMC, ราง Cable Tray, โคม High Bay LED"
        else:
            products = "ท่อร้อยสายไฟ EMT/IMC, รางวายเวย์, อุปกรณ์ข้อต่อ Coupling/Connector, ตู้คอนซูมเมอร์"
            
        score = min(score, 98)
        tier = "HOT" if score >= 80 else "WARM"
        code = f"ACTUAL_{int(payload.total_budget)}_{os.urandom(2).hex().upper()}"

        new_p = supabase.table("projects").insert({
            "project_code": code,
            "project_name": payload.project_name,
            "project_type": payload.project_type,
            "total_budget": payload.total_budget,
            "contractor_name": payload.contractor_name,
            "contractor_phone": payload.contractor_phone,
            "contractor_email": payload.contractor_email
        }).execute()

        if new_p.data:
            supabase.table("opportunities").insert({
                "project_id": new_p.data[0]["id"],
                "opportunity_score": score,
                "score_tier": tier,
                "recommended_products": products,
                "lead_status": "ยังไม่โทร",
                "score_reasons": {"sales_note": "บันทึกข้อมูลจริงจากหน้างาน"}
            }).execute()

        msg = f"⚡ [มีโครงการจริงเข้าระบบ]\nโครงการ: {payload.project_name}\nผู้รับเหมา: {payload.contractor_name}\nงบประมาณ: ฿{payload.total_budget:,.0f}\nระดับความเร่งด่วน: {score}/100 ({tier})\nเบอร์โทร: {payload.contractor_phone}"
        send_line_broadcast(msg)

        return {"status": "success", "message": "Lead created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.api_route("/api/sync-leads", methods=["GET", "POST"])
def sync_leads():
    """Endpoint สำหรับดึงข้อมูลอัตโนมัติ (ไม่มีการสุ่ม Mock Data)"""
    return {"status": "success", "message": "Ready for real integration feed", "new_count": 0}
