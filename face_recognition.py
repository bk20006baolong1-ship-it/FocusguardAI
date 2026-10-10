import numpy as np
import cv2
import json
from insightface.app import FaceAnalysis

class FaceRecognizer:
    def __init__(self, model_name='buffalo_sc'):
        # buffalo_sc is lightweight (MobileNet-based detection & recognition)
        # perfect for CPU and real-time execution.
        self.app = FaceAnalysis(name=model_name, root='~/.insightface')
        self.app.prepare(ctx_id=-1, det_size=(640, 640))
        
    def extract_embedding(self, face_image):
        """
        Extracts 512-d face embedding from a face image.
        Returns the embedding list, or None if no face is detected.
        """
        if face_image is None or face_image.size == 0:
            return None
        # insightface app.get expects BGR image
        faces = self.app.get(face_image)
        if len(faces) == 0:
            return None
        # Return the embedding of the largest face detected
        largest_face = max(faces, key=lambda x: (x.bbox[2] - x.bbox[0]) * (x.bbox[3] - x.bbox[1]))
        return largest_face.embedding.tolist()
        
    def register_student(self, student_images):
        """
        Takes a list of face image paths or numpy arrays.
        Extracts embedding for each image, averages them, and returns the averaged embedding.
        """
        embeddings = []
        for img in student_images:
            if isinstance(img, str):
                img_data = cv2.imread(img)
            else:
                img_data = img
                
            if img_data is not None and img_data.size > 0:
                emb = self.extract_embedding(img_data)
                if emb is not None:
                    embeddings.append(emb)
                    
        if len(embeddings) == 0:
            return None
            
        # Average the embeddings to build a robust prototype
        avg_embedding = np.mean(embeddings, axis=0)
        # L2 normalization
        norm = np.linalg.norm(avg_embedding)
        if norm > 0:
            avg_embedding = avg_embedding / norm
        return avg_embedding.tolist()
        
    def match_face(self, face_image, known_students, config=None):
        """Single-image match (no temporal confirmation) for offline tools.

        Uses the same threshold + margin rule as the live IdentityManager, so an
        ambiguous face returns ``(None, similarity)`` instead of a guess.
        """
        from focusguard.config import DEFAULT_CONFIG
        from focusguard.identity import IdentityManager
        emb = self.extract_embedding(face_image)
        if emb is None:
            return None, 0.0
        manager = IdentityManager({s['student_id']: s['embedding'] for s in known_students},
                                  config or DEFAULT_CONFIG.identity)
        result = manager.match(emb)
        return result.accepted_id, float(result.best_similarity)
