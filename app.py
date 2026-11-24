import os
import uuid
import json
import logging
from flask import Flask, request, jsonify, render_template, send_from_directory, make_response, Response
from dotenv import load_dotenv

# Load environment variables exclusively from local .env
load_dotenv(override=True)

from database import (
    init_db, save_analysis, get_history, get_analysis,
    delete_analysis, clear_history, save_follow_up,
    toggle_favorite, update_analysis_translation
)
from security import sanitize_and_process_image, sanitize_user_prompt, check_rate_limit
from gemini_service import gemini_service, SUPPORTED_LANGUAGES
from network_utils import get_local_ip_addresses
from tts_service import generate_audio

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("lifelens")

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "lifelens-default-secret-2026")
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024  # 16 MB max upload limit

# Initialize SQLite database on startup
init_db()


def get_client_session_id():
    """Identifies and enforces client session isolation."""
    session_id = request.headers.get("X-Session-ID") or request.cookies.get("lifelens_session")
    if not session_id:
        session_id = request.remote_addr or "127.0.0.1"
    return str(session_id).strip()


@app.route("/")
def index():
    response = make_response(render_template("index.html"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/manifest.json")
def manifest():
    return send_from_directory("static", "manifest.json", mimetype="application/manifest+json")


@app.route("/service-worker.js")
def service_worker():
    response = make_response(send_from_directory("static", "service-worker.js", mimetype="application/javascript"))
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    return response


@app.route("/api/status", methods=["GET"])
def api_status():
    """Read-only service status endpoint. Never returns API keys or credentials."""
    configured = gemini_service.is_configured()
    active_model = None
    if configured:
        try:
            active_model = gemini_service.resolve_model()
        except Exception as e:
            logger.warning(f"Failed to resolve model: {e}")

    port = int(os.environ.get("FLASK_PORT", 5000))
    local_ips = get_local_ip_addresses()

    return jsonify({
        "status": "online",
        "is_configured": configured,
        "active_model": active_model,
        "supported_languages": {k: {"name": v["name"], "native": v["native"]} for k, v in SUPPORTED_LANGUAGES.items()},
        "network_ips": local_ips,
        "port": port
    })


@app.route("/api/analyze", methods=["POST"])
def analyze():
    client_id = get_client_session_id()
    
    # 1. Rate limiting check
    allowed, retry_after = check_rate_limit(client_id)
    if not allowed:
        return jsonify({
            "error": f"Rate limit reached. Please wait {retry_after} seconds before analyzing another image."
        }), 429

    # 2. Check API key configuration in .env
    if not gemini_service.is_configured():
        return jsonify({
            "error": "Gemini API key is not configured in server .env file."
        }), 503

    # 3. Validate image upload (supports single or multiple images)
    files = request.files.getlist("images") or request.files.getlist("image")
    if not files and "image" in request.files:
        files = [request.files["image"]]
    if not files or not files[0].filename:
        return jsonify({"error": "No image file provided."}), 400

    user_question = sanitize_user_prompt(request.form.get("question", ""))
    language = request.form.get("language", "en")
    mode = request.form.get("mode", "quick")
    force_grounding = request.form.get("enable_search", "true").lower() == "true"
    session_id = client_id

    processed_images = []
    thumbnails = []
    for f in files[:4]:
        if not f.filename:
            continue
        try:
            img_bytes, mime_type, thumb_b64 = sanitize_and_process_image(f)
            processed_images.append((img_bytes, mime_type))
            thumbnails.append(thumb_b64)
        except ValueError as ve:
            return jsonify({"error": str(ve)}), 400
        except Exception as e:
            logger.error(f"Image processing error: {e}")
            return jsonify({"error": "Failed to process uploaded image."}), 400

    if not processed_images:
        return jsonify({"error": "No valid image could be processed."}), 400

    # 4. Call Gemini multimodal analysis
    try:
        result = gemini_service.analyze_image(
            images=processed_images,
            user_question=user_question,
            language=language,
            force_grounding=force_grounding,
            mode=mode
        )
    except Exception as e:
        logger.error(f"Analysis failed: {e}")
        err_msg = str(e)
        status_code = 500
        if "503" in err_msg or "UNAVAILABLE" in err_msg or "high demand" in err_msg:
            status_code = 503
            user_msg = "The AI service is temporarily experiencing high demand. Please try again shortly."
        elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            status_code = 429
            user_msg = "The AI service rate limit was momentarily exceeded. Please wait a moment and try again."
        elif "deadline" in err_msg.lower() or "timeout" in err_msg.lower():
            status_code = 504
            user_msg = "The AI analysis timed out due to temporary service load. Please try again."
        else:
            user_msg = f"AI analysis failed: {err_msg}"
        return jsonify({"error": user_msg}), status_code

    analysis_data = result["analysis"]
    grounding_data = result.get("grounding", [])
    is_grounded = result.get("is_grounded", False)
    primary_thumb = thumbnails[0] if thumbnails else ""

    # 5. Save to database with session isolation
    analysis_id = save_analysis(
        session_id=session_id,
        thumbnail_b64=json.dumps(thumbnails) if len(thumbnails) > 1 else primary_thumb,
        image_mime=processed_images[0][1],
        language=language,
        user_question=user_question,
        category=analysis_data.get("category", "general_assistance"),
        category_display=analysis_data.get("category_display_name", "General Object"),
        summary=analysis_data.get("summary", ""),
        urgency_level=analysis_data.get("urgency_level", "normal"),
        structured_data=analysis_data,
        grounding_data=grounding_data,
        is_grounded=is_grounded
    )

    return jsonify({
        "success": True,
        "analysis_id": analysis_id,
        "thumbnail_b64": primary_thumb,
        "thumbnails": thumbnails,
        "analysis": analysis_data,
        "grounding": grounding_data,
        "is_grounded": is_grounded,
        "model_used": result.get("model_used"),
        "language": language,
        "mode": mode
    })


@app.route("/api/follow-up", methods=["POST"])
def follow_up():
    session_id = get_client_session_id()
    allowed, retry_after = check_rate_limit(session_id)
    if not allowed:
        return jsonify({"error": f"Rate limit reached. Please wait {retry_after} seconds."}), 429

    data = request.get_json() or {}
    analysis_id = data.get("analysis_id")
    message = sanitize_user_prompt(data.get("message", ""))

    if not analysis_id or not message:
        return jsonify({"error": "Missing analysis ID or question."}), 400

    # Strict server-side session ownership check
    analysis_record = get_analysis(analysis_id, session_id=session_id)
    if not analysis_record:
        return jsonify({"error": "Analysis not found or access denied."}), 404

    # Save user's question verified by session_id
    try:
        save_follow_up(analysis_id, session_id, "user", message)
    except PermissionError:
        return jsonify({"error": "Session verification failed."}), 403

    try:
        reply = gemini_service.answer_follow_up(
            image_bytes=None,
            mime_type=None,
            previous_analysis=analysis_record["structured_data"],
            conversation_history=analysis_record.get("follow_ups", []),
            new_question=message,
            language=analysis_record.get("language", "en")
        )
        save_follow_up(analysis_id, session_id, "model", reply)
        return jsonify({
            "success": True,
            "reply": reply
        })
    except Exception as e:
        logger.error(f"Follow up failed: {e}")
        err_msg = str(e)
        status_code = 500
        if "503" in err_msg or "UNAVAILABLE" in err_msg or "high demand" in err_msg:
            status_code = 503
            user_msg = "The AI service is temporarily busy. Please try asking again shortly."
        elif "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            status_code = 429
            user_msg = "Rate limit reached. Please wait a moment and try again."
        else:
            user_msg = f"Failed to answer follow-up: {err_msg}"
        return jsonify({"error": user_msg}), status_code


@app.route("/api/tts", methods=["POST"])
def text_to_speech():
    """
    On-demand backend TTS endpoint.
    Only synthesizes the high-level headline & summary for the authorized session.
    Never exposes text in URL parameters or leaks sensitive OCR data.
    """
    session_id = get_client_session_id()
    data = request.get_json() or {}
    analysis_id = data.get("analysis_id")

    if not analysis_id:
        return jsonify({"error": "Missing analysis ID."}), 400

    record = get_analysis(analysis_id, session_id=session_id)
    if not record:
        return jsonify({"error": "Analysis not found or unauthorized."}), 404

    # Synthesize concise headline + summary only
    structured = record.get("structured_data", {})
    headline = structured.get("primary_headline", "")
    summary = structured.get("summary", "")
    text_to_speak = f"{headline}. {summary}".strip()
    language = record.get("language", "en")

    try:
        audio_bytes, mime = generate_audio(text_to_speak, language)
        return Response(audio_bytes, mimetype=mime, headers={
            "Content-Type": "audio/mpeg",
            "Cache-Control": "private, max-age=3600"
        })
    except Exception as e:
        logger.error(f"TTS synthesis failed: {e}")
        return jsonify({"error": f"Speech synthesis failed: {str(e)}"}), 500


@app.route("/api/translate", methods=["POST"])
def translate_analysis_endpoint():
    """Translates an existing analysis into target language using Gemini."""
    session_id = get_client_session_id()
    data = request.get_json() or {}
    analysis_id = data.get("analysis_id")
    target_language = data.get("target_language", "en")

    if not analysis_id or not target_language:
        return jsonify({"error": "Missing analysis ID or target language."}), 400

    record = get_analysis(analysis_id, session_id=session_id)
    if not record:
        return jsonify({"error": "Analysis not found or access denied."}), 404

    source_data = record.get("original_structured_data") or record["structured_data"]
    try:
        translated = gemini_service.translate_analysis(
            structured_data=source_data,
            target_language=target_language
        )
        update_analysis_translation(analysis_id, session_id, target_language, translated)
        return jsonify({
            "success": True,
            "analysis_id": analysis_id,
            "language": target_language,
            "analysis": translated
        })
    except Exception as e:
        logger.error(f"Translation failed: {e}")
        return jsonify({"error": f"Translation failed: {str(e)}"}), 500


@app.route("/api/history", methods=["GET"])
def history_list():
    session_id = get_client_session_id()
    saved = request.args.get("saved", "").lower() in ("true", "1")
    category = request.args.get("category")
    q = request.args.get("q")
    items = get_history(session_id, limit=50, favorites_only=saved, category=category, search=q)
    return jsonify({"history": items})


@app.route("/api/history/<analysis_id>/favorite", methods=["POST"])
def history_toggle_favorite(analysis_id):
    session_id = get_client_session_id()
    new_fav = toggle_favorite(analysis_id, session_id=session_id)
    if new_fav is None:
        return jsonify({"error": "Item not found or unauthorized."}), 404
    return jsonify({"success": True, "is_favorite": new_fav})


@app.route("/api/history/<analysis_id>", methods=["GET"])
def history_detail(analysis_id):
    session_id = get_client_session_id()
    item = get_analysis(analysis_id, session_id=session_id)
    if not item:
        return jsonify({"error": "Item not found or access denied."}), 404
    return jsonify(item)


@app.route("/api/history/<analysis_id>", methods=["DELETE"])
def history_delete(analysis_id):
    session_id = get_client_session_id()
    deleted = delete_analysis(analysis_id, session_id=session_id)
    return jsonify({"success": deleted})


@app.route("/api/history", methods=["DELETE"])
def history_clear_all():
    session_id = get_client_session_id()
    count = clear_history(session_id)
    return jsonify({"success": True, "deleted_count": count})


if __name__ == "__main__":
    port = int(os.environ.get("FLASK_PORT", 5000))
    host = os.environ.get("FLASK_HOST", "0.0.0.0")
    print(f"Starting LifeLens AI on http://localhost:{port}")
    app.run(host=host, port=port, debug=False)
