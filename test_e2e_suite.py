import urllib.request
import urllib.parse
import json
import io
import time
from PIL import Image, ImageDraw

BASE_URL = "http://127.0.0.1:5000"

def create_test_image(text, bg_color=(240, 240, 240), text_color=(20, 20, 20), size=(640, 480)):
    im = Image.new("RGB", size, color=bg_color)
    draw = ImageDraw.Draw(im)
    draw.rectangle([20, 20, size[0]-20, size[1]-20], outline=(100, 100, 100), width=3)
    draw.text((40, size[1]//3), text, fill=text_color)
    buf = io.BytesIO()
    im.save(buf, format="JPEG", quality=90)
    return buf.getvalue()

def multipart_post(url, fields, files):
    boundary = "----WebKitFormBoundary" + str(int(time.time()))
    body = bytearray()
    for name, value in fields.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"\r\n\r\n{value}\r\n".encode("utf-8"))
    for name, (filename, content, mime) in files.items():
        body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"{name}\"; filename=\"{filename}\"\r\nContent-Type: {mime}\r\n\r\n".encode("utf-8"))
        body.extend(content)
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    
    last_err = None
    for attempt in range(3):
        try:
            req = urllib.request.Request(url, data=bytes(body), headers={
                "Content-Type": f"multipart/form-data; boundary={boundary}",
                "X-Session-ID": "test-session-e2e-2026"
            })
            with urllib.request.urlopen(req, timeout=60) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            last_err = e
            time.sleep(3.0)
    raise last_err

def test_status():
    print("\n--- 1. Testing /api/status & UI ---")
    req = urllib.request.Request(f"{BASE_URL}/api/status")
    with urllib.request.urlopen(req) as resp:
        st = json.loads(resp.read().decode("utf-8"))
        print(f"Status: {st['status']}, Active Model: {st['active_model']}, Configured: {st['is_configured']}")
        assert st["status"] == "online"
        assert st["is_configured"] is True

    with urllib.request.urlopen(f"{BASE_URL}/") as resp:
        html = resp.read().decode("utf-8")
        assert "LifeLens AI" in html
        assert "DEMO" not in html
        assert "Demo scenario" not in html
        assert "data-mode" in html
        assert "aPlay" in html
        print("Frontend HTML verified: Clean, 0 DEMO datasets, Claude UI layout active.")

def test_analysis_scenarios():
    print("\n--- 2. Testing Real Gemini Multimodal Analysis Scenarios ---")
    scenarios = [
        ("Appliance Error", "Whirlpool Washer Front Panel: ERROR F21 DRAIN FAILURE", "problem_solving", "en", "quick"),
        ("Store Receipt", "GROCERY RECEIPT\nFresh Supermarket\nMilk: $3.50\nBread: $2.50\nTotal: $6.00", "information_extraction", "en", "quick"),
        ("Plant Care", "Monstera Deliciosa Plant in Pot with some yellowing lower leaves", "general_assistance", "en", "detailed"),
        ("Scam SMS", "URGENT: Your SBI Bank account is locked. Click bit.ly/bank-unlock to update KYC", "problem_solving", "en", "quick"),
        ("Multilingual Kannada", "Karnataka Road Notice: ನಿಧಾನವಾಗಿ ಚಲಿಸಿ ಶಾಲೆ ಇದೆ (Drive slowly school zone)", "information_extraction", "kn", "quick")
    ]

    analyses = []
    for title, content, expected_archetype, lang, mode in scenarios:
        t0 = time.time()
        img_bytes = create_test_image(content)
        res = multipart_post(f"{BASE_URL}/api/analyze", {
            "question": f"What is this and what should I do? (Testing {title})",
            "language": lang,
            "mode": mode,
            "enable_search": "false"
        }, {
            "images": ("test.jpg", img_bytes, "image/jpeg")
        })
        duration = time.time() - t0
        analysis = res["analysis"]
        print(f"[{title}] {duration:.2f}s | Archetype: {analysis.get('intent_type')} | Cat: {analysis.get('category_display_name')} | Blocks: {len(analysis.get('blocks', []))}")
        assert res["success"] is True
        assert "primary_headline" in analysis
        assert "summary" in analysis
        assert len(analysis.get("blocks", [])) > 0
        analyses.append(res)
        time.sleep(1.0) # Graceful pacing

    return analyses

def test_multi_image_comparison():
    print("\n--- 3. Testing Multi-Image Comparison (2 images) ---")
    img1 = create_test_image("Charger A: 20W Compact USB-C Adapter, Price: $15", bg_color=(230, 240, 255))
    img2 = create_test_image("Charger B: 65W GaN Dual USB-C Fast Charger, Price: $35", bg_color=(255, 240, 230))
    
    t0 = time.time()
    boundary = "----WebKitBoundaryMulti" + str(int(time.time()))
    body = bytearray()
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"question\"\r\n\r\nCompare these two chargers\r\n".encode("utf-8"))
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"language\"\r\n\r\nen\r\n".encode("utf-8"))
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"mode\"\r\n\r\ndetailed\r\n".encode("utf-8"))
    
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"images\"; filename=\"charger_a.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode("utf-8"))
    body.extend(img1)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}\r\nContent-Disposition: form-data; name=\"images\"; filename=\"charger_b.jpg\"\r\nContent-Type: image/jpeg\r\n\r\n".encode("utf-8"))
    body.extend(img2)
    body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode("utf-8"))
    
    req = urllib.request.Request(f"{BASE_URL}/api/analyze", data=bytes(body), headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Session-ID": "test-session-e2e-2026"
    })
    with urllib.request.urlopen(req, timeout=60) as resp:
        res = json.loads(resp.read().decode("utf-8"))
    duration = time.time() - t0
    analysis = res["analysis"]
    print(f"Multi-image comparison ({duration:.2f}s) | Headline: {analysis.get('primary_headline')} | Blocks: {len(analysis.get('blocks', []))}")
    assert res["success"] is True
    assert len(res.get("thumbnails", [])) == 2
    return res

def test_followup_and_translation(analysis_id):
    print("\n--- 4. Testing Follow-up Conversation & Post-Analysis Translation ---")
    # Follow-up
    t0 = time.time()
    f_data = json.dumps({"analysis_id": analysis_id, "message": "Can I fix this at home safely?"}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/api/follow-up", data=f_data, headers={
        "Content-Type": "application/json",
        "X-Session-ID": "test-session-e2e-2026"
    })
    with urllib.request.urlopen(req, timeout=45) as resp:
        f_res = json.loads(resp.read().decode("utf-8"))
    print(f"Follow-up answered ({time.time()-t0:.2f}s): {f_res['reply'][:90]}...")
    assert f_res["success"] is True

    # Translation
    t0 = time.time()
    tr_data = json.dumps({"analysis_id": analysis_id, "target_language": "hi"}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/api/translate", data=tr_data, headers={
        "Content-Type": "application/json",
        "X-Session-ID": "test-session-e2e-2026"
    })
    with urllib.request.urlopen(req, timeout=45) as resp:
        tr_res = json.loads(resp.read().decode("utf-8"))
    print(f"Translation to Hindi ({time.time()-t0:.2f}s): Headline: {tr_res['analysis'].get('primary_headline')}")
    assert tr_res["success"] is True
    assert tr_res["language"] == "hi"

def test_audio_playback(analysis_id):
    print("\n--- 5. Testing Multilingual TTS Audio Stream ---")
    tts_data = json.dumps({"analysis_id": analysis_id}).encode("utf-8")
    req = urllib.request.Request(f"{BASE_URL}/api/tts", data=tts_data, headers={
        "Content-Type": "application/json",
        "X-Session-ID": "test-session-e2e-2026"
    })
    with urllib.request.urlopen(req, timeout=45) as resp:
        content_type = resp.headers.get("Content-Type")
        audio_bytes = resp.read()
        print(f"Audio received: {len(audio_bytes)} bytes, Content-Type: {content_type}")
        assert len(audio_bytes) > 500
        assert "audio" in content_type

def test_favorites_and_history(analysis_id):
    print("\n--- 6. Testing History, Favourites Toggle & Deletion ---")
    # Favorite toggle
    req = urllib.request.Request(f"{BASE_URL}/api/history/{analysis_id}/favorite", data=b"{}", headers={
        "Content-Type": "application/json",
        "X-Session-ID": "test-session-e2e-2026"
    })
    with urllib.request.urlopen(req) as resp:
        fav_res = json.loads(resp.read().decode("utf-8"))
        print(f"Favorite toggled: {fav_res['is_favorite']}")
        assert fav_res["is_favorite"] is True

    # Check saved history filter
    req = urllib.request.Request(f"{BASE_URL}/api/history?saved=true", headers={"X-Session-ID": "test-session-e2e-2026"})
    with urllib.request.urlopen(req) as resp:
        h_res = json.loads(resp.read().decode("utf-8"))
        print(f"Saved analyses in DB: {len(h_res['history'])}")
        assert any(item["id"] == analysis_id for item in h_res["history"])

    # Delete record
    req = urllib.request.Request(f"{BASE_URL}/api/history/{analysis_id}", headers={"X-Session-ID": "test-session-e2e-2026"}, method="DELETE")
    with urllib.request.urlopen(req) as resp:
        del_res = json.loads(resp.read().decode("utf-8"))
        print(f"Deleted test record: {del_res['success']}")
        assert del_res["success"] is True

if __name__ == "__main__":
    test_status()
    analyses = test_analysis_scenarios()
    first_id = analyses[0]["analysis_id"]
    test_multi_image_comparison()
    test_followup_and_translation(first_id)
    test_audio_playback(first_id)
    test_favorites_and_history(first_id)
    print("\n==========================================")
    print("ALL TESTS PASSED WITH 100% SUCCESS!")
    print("==========================================")
