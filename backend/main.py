from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from database import Base, engine
from api.auth import router as auth_router
from api.projects import router as projects_router

app = FastAPI(title="User Authentication API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

Base.metadata.create_all(bind=engine)

app.include_router(auth_router, tags=["Authentication"])
app.include_router(projects_router, tags=["Projects"])

@app.get("/")
def root():
    return {"message": "Backend is running!"}
