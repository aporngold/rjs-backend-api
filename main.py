import os
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client

app = FastAPI(title="RJSE Live e-GP Procurement API")

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
OPEND_API_KEY = os.environ.get("OPEND_API_KEY")

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
        payload = {"messages": [{"type": "text", "text": message}]}
        requests.post(url, headers=headers, json=payload, timeout=5)
    except Exception as e:
        print(f"LINE Error: {e}")

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
    return {"status": "online", "message": "RJSE Live e-GP System Running with OpenData Key"}

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
            send_line_broadcast(f"🎯 [อัปเดตงานขาย]: {payload.new_status}\nโครงการ: {payload.project_name}\nติดตามผลด่วนในระบบ RJSE Dashboard")

        return {"status": "success", "message": "Status updated"}
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
        return {"status": "success", "message": "Note saved"}
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
        code = f"MANUAL_{int(payload.total_budget)}_{os.urandom(2).hex().upper()}"

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
                "score_reasons": {"sales_note": "บันทึกจากหน้างาน"}
            }).execute()

        send_line_broadcast(f"⚡ [มีโครงการใหม่เข้าระบบ]\nโครงการ: {payload.project_name}\nผู้รับเหมา: {payload.contractor_name}\nงบประมาณ: ฿{payload.total_budget:,.0f}\nคะแนน: {score}/100 ({tier})\nโทร: {payload.contractor_phone}")

        return {"status": "success", "message": "Lead created"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.api_route("/api/sync-leads", methods=["GET", "POST"])
def fetch_real_government_procurement():
    """ดึงข้อมูลโครงการจัดซื้อจัดจ้างงานระบบไฟฟ้าจริงจาก Open Data ภาครัฐโดยใช้ API Key ที่ได้รับ"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    new_count = 0
    keywords = ["ระบบไฟฟ้า", "หม้อแปลง", "ตู้ MDB", "สายไฟฟ้า", "ท่อร้อยสายไฟ"]
    api_key = OPEND_API_KEY or "8Kng6N79PxIOP79qcSGSfdQWTyiHolQj"
    
    try:
        for kw in keywords:
            # ยิงดึงข้อมูลจาก Open Government Data API
            url = f"https://opend.data.go.th/govspending/cgdcontract?api-key={api_key}&keyword={kw}&limit=15"
            try:
                res = requests.get(url, timeout=8)
                if res.status_code == 200:
                    resp_json = res.json()
                    # รองรับทั้ง result list และ data wrapper
                    data = resp_json.get("result") or resp_json.get("data") or []
                    for item in data:
                        proj_id = str(item.get("project_id") or item.get("project_number") or f"GOV_{item.get('project_name', '')[:10]}")
                        proj_name = item.get("project_name")
                        dept_name = item.get("dept_name") or item.get("department_name") or "หน่วยงานภาครัฐ"
                        budget = float(item.get("sum_price_agree") or item.get("budget_total") or item.get("contract_money") or 0)
                        winner = item.get("winner") or item.get("contractor_name") or "รอประกาศผลผู้ชนะ/ผู้รับเหมา"
                        
                        if not proj_name or budget <= 0:
                            continue

                        # เช็กว่าเคยบันทึกโครงการนี้ไปแล้วหรือยัง
                        existing = supabase.table("projects").select("id").eq("project_code", proj_id).execute()
                        if not existing.data:
                            score = 65
                            if budget >= 10000000: score += 25
                            elif budget >= 2000000: score += 15
                            if any(k in proj_name for k in ["โรงงาน", "สถานีไฟฟ้าย่อย", "อาคารสูง"]): score += 10
                            score = min(score, 98)
                            tier = "HOT" if score >= 80 else "WARM"

                            new_p = supabase.table("projects").insert({
                                "project_code": proj_id,
                                "project_name": proj_name,
                                "project_type": f"จัดซื้อจัดจ้างภาครัฐ ({dept_name})",
                                "total_budget": budget,
                                "contractor_name": winner,
                                "contractor_phone": "ติดต่อฝ่ายพัสดุ/จัดซื้อ",
                                "contractor_email": ""
                            }).execute()

                            if new_p.data:
                                supabase.table("opportunities").insert({
                                    "project_id": new_p.data[0]["id"],
                                    "opportunity_score": score,
                                    "score_tier": tier,
                                    "recommended_products": "ท่อร้อยสายไฟ EMT/IMC/RSC, รางวายเวย์ มอก., ตู้คอนโทรล MDB, อุปกรณ์ข้อต่อและฟิตติ้ง",
                                    "lead_status": "ยังไม่โทร",
                                    "score_reasons": {"sales_note": f"ประกาศจัดซื้อจาก: {dept_name}"}
                                }).execute()
                                new_count += 1
            except Exception as sub_e:
                print(f"Fetch kw {kw} error: {sub_e}")
                continue

        return {"status": "success", "new_count": new_count}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
