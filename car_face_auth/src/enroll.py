import os
import pickle 
from pathlib import Path
import cv2
import numpy as np
from insightface.app import FaceAnalysis

DATA_DIR = Path("data")
EMBEDDINGS_DIR = DATA_DIR / "embeddings"
EMBEDDINGS_FILE = EMBEDDINGS_DIR / "face_embeddings.pkl"

def load_database():
    if EMBEDDINGS_FILE.exists():
        with open(EMBEDDINGS_FILE, "rb") as f:
            return pickle.load(f)
    return {}

