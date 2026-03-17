import cv2
import numpy as np
from insightface.app import FaceAnalysis

def main():
    app = FaceAnalysis(name="buffalo_l")
    app.prepare(ctx_id=0, det_size=(640, 640))

    cap = cv2.VideoCapture(0)
    if not cap.isOpened():
        print("Cannot open camera")
        return
    
    while True:
        ret, frame = cap.read()
        if not ret:
            print("Can't receive frame (stream end?). Exiting ...")
            break

        faces = app.get(frame) # Gets bbox and embedded vectors 
        for face in faces:
            bbox = face.bbox.astype(int)
            x1, y1, x2, y2 = bbox
            cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)

            embedding = face.embedding
            emb_norm = np.linalg.norm(embedding)

            label = f"Face | emb_dim={embedding.shape[0]} | norm={emb_norm:.2f}"
            cv2.putText(frame, label, (x1, max(y1-10, 20)), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2,)
            print(f"Detected face with embedding shape = {embedding.shape} and norm = {emb_norm:.4f}")

        cv2.imshow('InsightFace Test', frame)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
    cap.release()
    cv2.destroyAllWindows()

if __name__ == "__main__":    main()