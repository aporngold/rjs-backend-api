import os
import requests
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
from supabase import create_client, Client

app = FastAPI(title="RJSE Live Procurement API (Production)")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ดึงค่าความปลอดภัยจาก Environment Variables บน Render เท่านั้น
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
        print(f"LINE Broadcast Error: {e}")

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
    return {"status": "online", "message": "RJSE Live Backend API is running securely"}

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
                "score_reasons": {"sales_note": "บันทึกจากหน้างานจริง"}
            }).execute()

        send_line_broadcast(f"⚡ [มีโครงการจริงเข้าระบบ]\nโครงการ: {payload.project_name}\nผู้รับเหมา: {payload.contractor_name}\nงบประมาณ: ฿{payload.total_budget:,.0f}\nคะแนน: {score}/100 ({tier})\nโทร: {payload.contractor_phone}")

        return {"status": "success", "message": "Lead created successfully"}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.api_route("/api/sync-leads", methods=["GET", "POST"])
def fetch_real_government_procurement():
    """ดึงข้อมูลโครงการจริงงานระบบไฟฟ้าจาก Open Data ภาครัฐอย่างปลอดภัย"""
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")

    api_key = OPEND_API_KEY
    if not api_key:
        print("Warning: OPEND_API_KEY not configured in Render environment.")
        return {"status": "error", "message": "API Key not configured", "new_count": 0}

    new_count = 0
    keywords = ["ระบบไฟฟ้า", "หม้อแปลงไฟฟ้า", "ตู้ MDB", "สายไฟฟ้า", "ท่อร้อยสายไฟ"]
    fetched_projects = []

    # ดึงข้อมูลจาก Open Data Portal
    for kw in keywords:
        endpoints = [
            f"https://opend.data.go.th/govspending/cgdcontract?api-key={api_key}&keyword={kw}&limit=20",
            f"https://govspending.data.go.th/api/service/cgdcontract?api-key={api_key}&keyword={kw}&limit=20"
        ]
        for url in endpoints:
            try:
                headers = {"api-key": api_key, "User-Agent": "Mozilla/5.0"}
                res = requests.get(url, headers=headers, timeout=6)
                if res.status_code == 200:
                    resp_json = res.json()
                    items = resp_json.get("result") or resp_json.get("data") or []
                    if isinstance(items, list) and len(items) > 0:
                        fetched_projects.extend(items)
                        break
            except Exception as e:
                print(f"API Fetch Error ({kw}): {e}")
                continue

    # บันทึกข้อมูลโครงการจริงเข้า Supabase
    for item in fetched_projects:
        proj_id = str(item.get("project_id") or item.get("project_number") or "")
        proj_name = item.get("project_name") or item.get("project_name_th")
        dept_name = item.get("dept_name") or item.get("department_name") or "หน่วยงานภาครัฐ"
        
        raw_budget = item.get("sum_price_agree") or item.get("budget_total") or item.get("contract_money") or 0
        try:
            budget = float(str(raw_budget).replace(",", ""))
        except:
            budget = 0.0

        winner = item.get("winner") or item.get("contractor_name") or "ผู้รับเหมาผู้ชนะการเสนอราคา"

        if not proj_id or not proj_name or budget <= 0:
            continue

        try:
            existing = supabase.table("projects").select("id").eq("project_code", proj_id).execute()
            if not existing.data:
                score = 65
                if budget >= 10000000:
                    score += 25
                elif budget >= 2000000:
                    score += 15
                if any(k in proj_name for k in ["โรงงาน", "สถานีไฟฟ้าย่อย", "อาคารสูง", "MDB"]):
                    score += 10
                    
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
                        "score_reasons": {"sales_note": f"ประกาศจัดซื้อจริงจาก: {dept_name}"}
                    }).execute()
                    new_count += 1
        except Exception as insert_e:
            print(f"Insert Lead error: {insert_e}")
            continue

    return {"status": "success", "new_count": new_count, "fetched_total": len(fetched_projects)}
