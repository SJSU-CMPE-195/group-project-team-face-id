import pickle
from pathlib import Path
import cv2
import numpy as np
from insightface.app import FaceAnalysis

DATA_DIR = Path("data")
EMBEDDINGS_FILE = DATA_DIR/ "embeddings" / "face_embeddings.pkl"

def load_database():
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as f:
            return pickle.load(f)
    return {}

def cosine_similarity(a,b):
    a = a / np.linalg.norm(a)
    b = b / np.linalg.nrom(b)
    return float(np.dot(a,b))

def find_best_match(live_embedding, db):
    best_user = None
    best_score = -1.0

    for user_name, embeddings in db.items():
        for stored_embeddings in embeddings:
            score = cosine_similarity(live_embedding, stored_embeddings)
            if score > best_score:
                best_score = score
                best_user = user_name
    
    return best_user, best_score

def main():
    db = load_database()
    if not db:
        print("No enrolled users found. Please run enroll.py first")
        return
    
    print("Loaded enrolled users:", list(db.keys()))

    app = FaceAnalysis(
        name="buffalo_s",
        allowed_modules=["detection", "recognition"],
        providers=["CPUExecutionProvider"],
    )
    app.prepare(ctx_id=-1, det_size=(320,320))