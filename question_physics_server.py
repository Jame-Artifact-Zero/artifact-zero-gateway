# question_physics_server.py
# Standalone Question Physics Service
# Runs on separate port, no changes to main app.py needed
#
# Usage:
# python question_physics_server.py
# Service runs on port 5001
# Frontend calls this directly for Question Physics analysis

from flask import Flask, request, jsonify
from flask_cors import CORS
import os
from question_physics import question_physics_analyze

# Create standalone Flask app
app = Flask(__name__)
CORS(app)  # Allow cross-origin requests from main app

@app.route('/analyze', methods=['POST'])
def analyze_question():
    """Analyze question physics for given text"""
    try:
        payload = request.get_json(silent=True) or {}
        text = payload.get('text') or payload.get('input') or ''
        
        if not text:
            return jsonify({"error": "No text provided"}), 400
        
        # Run Question Physics analysis
        result = question_physics_analyze(text)
        
        return jsonify({
            "status": "ok",
            "question_physics": result,
            "service": "question_physics_server",
            "version": "1.0"
        })
        
    except Exception as e:
        return jsonify({
            "status": "error",
            "error": str(e)
        }), 500

@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({
        "status": "ok",
        "service": "question_physics_server",
        "version": "1.0"
    })

@app.route('/test', methods=['GET'])
def test_endpoint():
    """Test the Question Physics analysis with sample data"""
    test_cases = [
        "so what does this system do for numbers and things?",
        "is this just fancy marketing words?",
        "convince me this is worth the investment",
        "can you clarify what you mean?"
    ]
    
    results = []
    for text in test_cases:
        analysis = question_physics_analyze(text)
        results.append({
            "text": text,
            "analysis": analysis
        })
    
    return jsonify({
        "status": "ok",
        "test_results": results
    })

if __name__ == '__main__':
    # Run on port 5001 to avoid conflict with main app
    port = int(os.environ.get('QUESTION_PHYSICS_PORT', 5001))
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False
    )
