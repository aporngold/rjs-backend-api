import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from supabase import create_client, Client

app = FastAPI(title="RJSE Secure API")

# อนุญาตให้หน้าเว็บทุกที่เรียกใช้ API นี้ได้
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ดึง Key จากระบบความปลอดภัยของ Render
SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_KEY = os.environ.get("SUPABASE_KEY")

supabase: Client = None
if SUPABASE_URL and SUPABASE_KEY:
    supabase = create_client(SUPABASE_URL, SUPABASE_KEY)

class StatusUpdate(BaseModel):
    opportunity_id: str
    new_status: str

@app.get("/")
def root():
    return {"status": "online", "message": "RJSE Backend API is running"}

# API ส่งข้อมูลโครงการไปแสดงที่หน้าเว็บ
@app.get("/api/projects")
def get_all_projects():
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
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
            lead_status
        )
    """).order("created_at", ascending=False).execute()
    
    return {"status": "success", "data": res.data}

# API บันทึกสถานะการโทร
@app.post("/api/update-status")
def update_lead_status(payload: StatusUpdate):
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    res = supabase.table("opportunities").update({
        "lead_status": payload.new_status
    }).eq("id", payload.opportunity_id).execute()
    
    return {"status": "success", "message": "Updated successfully"}

# API ดึงโครงการสดใหม่เข้า Supabase
@app.post("/api/sync-leads")
def sync_leads():
    if not supabase:
        raise HTTPException(status_code=500, detail="Database not configured")
    
    fresh_leads = [
        {
            "code": "REAL_IND_SKN_2026",
            "name": "งานติดตั้งระบบไฟฟ้ากำลัง ตู้ MDB และหม้อแปลง โรงงานผลิตและคลังสินค้า",
            "type": "โรงงานอุตสาหกรรม / ภาคเอกชน",
            "budget": 16500000,
            "contractor": "บริษัท เอส เค เอ็น เพาเวอร์ เอ็นจิเนียริ่ง จำกัด",
            "phone": "02-465-2899",
            "email": "skn_power@yahoo.com",
            "products": "หม้อแปลงไฟฟ้า, ตู้ MDB, ราง Cable Tray, ท่อหนา RSC, โคม High Bay LED",
            "score": 88,
            "tier": "HOT"
        },
        {
            "code": "REAL_COMM_PPOWER_2026",
            "name": "รับเหมาติดตั้งเดินระบบไฟฟ้าแรงต่ำ-สูง ตู้สวิตช์บอร์ด อาคารสำนักงานและโกดัง",
            "type": "อาคารพาณิชย์ / ผู้รับเหมาไฟฟ้าเอกชน",
            "budget": 8900000,
            "contractor": "พี-เพาเวอร์ โซลูชั่นส์ (P-Power Solutions)",
            "phone": "091-067-6398",
            "email": "ppowersolutions1999@gmail.com",
            "products": "รางแลดเดอร์, ท่อร้อยสายไฟ HDPE/IMC, ตู้คอนซูมเมอร์, สาย THW/NYY",
            "score": 78,
            "tier": "WARM"
        }
    ]

    new_count = 0
    for item in fresh_leads:
        existing = supabase.table("projects").select("id").eq("project_code", item["code"]).execute()
        if not existing.data:
            new_p = supabase.table("projects").insert({
                "project_code": item["code"],
                "project_name": item["name"],
                "project_type": item["type"],
                "total_budget": item["budget"],
                "contractor_name": item["contractor"],
                "contractor_phone": item["phone"],
                "contractor_email": item["email"]
            }).execute()

            if new_p.data:
                supabase.table("opportunities").insert({
                    "project_id": new_p.data[0]["id"],
                    "opportunity_score": item["score"],
                    "score_tier": item["tier"],
                    "recommended_products": item["products"],
                    "lead_status": "ยังไม่โทร"
                }).execute()
                new_count += 1

    return {"status": "success", "new_count": new_count}
