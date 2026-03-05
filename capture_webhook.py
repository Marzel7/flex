"""
Capture raw webhook request headers and body for inspection
"""
from flask import Flask, request
import json
import sys

app = Flask(__name__)

@app.route('/helius/webhook', methods=['POST'])
def capture_webhook():
    print("\n" + "="*80)
    print("INCOMING WEBHOOK REQUEST")
    print("="*80)
    
    # Headers
    print("\n📋 HEADERS:")
    for header, value in request.headers:
        # Redact sensitive info
        if 'auth' in header.lower() or 'x-' in header.lower():
            print(f"  {header}: [REDACTED]")
        else:
            print(f"  {header}: {value}")
    
    # Body metadata
    print(f"\n📦 BODY METADATA:")
    print(f"  Content-Type: {request.content_type}")
    print(f"  Content-Length: {request.content_length}")
    
    # Raw body
    try:
        body = request.get_data(as_text=True)
        print(f"\n📝 RAW BODY LENGTH: {len(body)} chars")
        
        # Parse and show top-level structure
        data = json.loads(body)
        if isinstance(data, list):
            print(f"  Type: LIST with {len(data)} items")
            if data:
                print(f"\n  First item top-level keys:")
                for key in data[0].keys():
                    print(f"    - {key}")
        else:
            print(f"  Type: DICT")
            print(f"\n  Top-level keys:")
            for key in data.keys():
                print(f"    - {key}")
        
        print("\n✅ Webhook captured. Exiting.")
        sys.exit(0)
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return "error", 500

if __name__ == '__main__':
    print("🟢 Listening on port 5002/helius/webhook")
    print("Send one webhook and it will print headers+structure, then exit")
    app.run(port=5002, debug=False)
