import os
import sys

print("Loading InsightFace 'buffalo_sc' to trigger auto-download...")
try:
    from insightface.app import FaceAnalysis
    
    # Initialize model to trigger auto-download in home directory ~/.insightface
    app = FaceAnalysis(name='buffalo_sc', root='~/.insightface')
    app.prepare(ctx_id=-1, det_size=(640, 640))
    
    print("\n[SUCCESS] InsightFace 'buffalo_sc' downloaded and prepared successfully!")
except Exception as e:
    print(f"\n[ERROR] Error downloading model: {e}")
    sys.exit(1)
