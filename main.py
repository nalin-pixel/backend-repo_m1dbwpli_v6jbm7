import os
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Optional
from bson import ObjectId

app = FastAPI(title="Restaurant Ordering API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Import database helpers
try:
    from database import db
except Exception:
    db = None

# Pydantic models aligned with schemas
class OrderItem(BaseModel):
    item_id: str
    quantity: int

class CreateOrder(BaseModel):
    customer_name: str
    customer_email: str
    customer_address: str
    items: List[OrderItem]
    notes: Optional[str] = None

@app.get("/")
def read_root():
    return {"message": "Restaurant API running"}

@app.get("/test")
def test_database():
    response = {
        "backend": "✅ Running",
        "database": "❌ Not Available",
        "database_url": None,
        "database_name": None,
        "connection_status": "Not Connected",
        "collections": []
    }

    try:
        from database import db as _db
        if _db is not None:
            response["database"] = "✅ Available"
            response["database_url"] = "✅ Set" if os.getenv("DATABASE_URL") else "❌ Not Set"
            response["database_name"] = _db.name if hasattr(_db, 'name') else "✅ Connected"
            response["connection_status"] = "Connected"
            try:
                collections = _db.list_collection_names()
                response["collections"] = collections[:10]
                response["database"] = "✅ Connected & Working"
            except Exception as e:
                response["database"] = f"⚠️  Connected but Error: {str(e)[:50]}"
        else:
            response["database"] = "⚠️  Available but not initialized"
    except Exception as e:
        response["database"] = f"❌ Error: {str(e)[:80]}"

    return response

# Utility to convert ObjectId to string

def serialize_doc(doc):
    if not doc:
        return doc
    doc = dict(doc)
    if doc.get("_id"):
        doc["id"] = str(doc.pop("_id"))
    return doc

# Menu Endpoints
@app.get("/api/menu")
async def list_menu(category: Optional[str] = None):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    filter_query = {"available": True}
    if category:
        filter_query["category"] = category
    items = list(db["menuitem"].find(filter_query))
    return [serialize_doc(i) for i in items]

@app.post("/api/menu")
async def add_menu_item(item: dict):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    result = db["menuitem"].insert_one(item)
    created = db["menuitem"].find_one({"_id": result.inserted_id})
    return serialize_doc(created)

@app.post("/api/menu/seed")
async def seed_menu():
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    count = db["menuitem"].count_documents({})
    if count > 0:
        items = list(db["menuitem"].find({"available": True}))
        return {"seeded": False, "count": count, "items": [serialize_doc(i) for i in items]}

    sample_items = [
        {"name": "Margherita Pizza", "description": "Classic with fresh mozzarella, basil, and tomatoes", "price": 11.99, "category": "Pizza", "image": "https://images.unsplash.com/photo-1548365328-9f547fb09520", "available": True},
        {"name": "Pepperoni Pizza", "description": "Loaded with pepperoni and cheese", "price": 13.49, "category": "Pizza", "image": "https://images.unsplash.com/photo-1601924582971-b0c5be3eebc9", "available": True},
        {"name": "Veggie Burger", "description": "Grilled veggie patty with avocado", "price": 9.99, "category": "Burgers", "image": "https://images.unsplash.com/photo-1550547660-d9450f859349", "available": True},
        {"name": "Cheeseburger", "description": "Beef patty, cheddar, pickles, house sauce", "price": 10.99, "category": "Burgers", "image": "https://images.unsplash.com/photo-1551782450-17144c3a8f59", "available": True},
        {"name": "Caesar Salad", "description": "Romaine, parmesan, croutons, creamy dressing", "price": 8.49, "category": "Salads", "image": "https://images.unsplash.com/photo-1551183053-bf91a1d81141", "available": True},
        {"name": "Lemonade", "description": "Freshly squeezed, lightly sweetened", "price": 3.49, "category": "Drinks", "image": "https://images.unsplash.com/photo-1497534547324-0ebb3f052e88", "available": True}
    ]
    db["menuitem"].insert_many(sample_items)
    items = list(db["menuitem"].find({"available": True}))
    return {"seeded": True, "count": len(items), "items": [serialize_doc(i) for i in items]}

# Order Endpoints
@app.post("/api/orders")
async def create_order(order: CreateOrder):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")

    # Validate that items exist and compute totals
    try:
        menu_ids = [ObjectId(oi.item_id) for oi in order.items]
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid item id format")

    menu_docs_cursor = db["menuitem"].find({"_id": {"$in": menu_ids}})
    menu_docs = {str(d["_id"]): d for d in menu_docs_cursor}

    if len(menu_docs) != len(order.items):
        raise HTTPException(status_code=400, detail="One or more items not found")

    line_items = []
    subtotal = 0.0
    for oi in order.items:
        doc = menu_docs.get(oi.item_id)
        if doc is None:
            doc = db["menuitem"].find_one({"_id": ObjectId(oi.item_id)})
        if doc is None:
            raise HTTPException(status_code=400, detail="Invalid item id")
        price = float(doc.get("price", 0))
        line_total = price * oi.quantity
        subtotal += line_total
        line_items.append({
            "item_id": oi.item_id,
            "name": doc.get("name"),
            "price": price,
            "quantity": oi.quantity,
            "line_total": round(line_total, 2)
        })

    tax = round(subtotal * 0.08, 2)
    total = round(subtotal + tax, 2)

    order_doc = {
        "customer_name": order.customer_name,
        "customer_email": order.customer_email,
        "customer_address": order.customer_address,
        "items": line_items,
        "subtotal": round(subtotal, 2),
        "tax": tax,
        "total": total,
        "status": "pending"
    }

    res = db["order"].insert_one(order_doc)
    created = db["order"].find_one({"_id": res.inserted_id})
    return serialize_doc(created)

@app.get("/api/orders")
async def list_orders(limit: int = 20):
    if db is None:
        raise HTTPException(status_code=500, detail="Database not initialized")
    orders = list(db["order"].find().sort("_id", -1).limit(limit))
    return [serialize_doc(o) for o in orders]

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)
