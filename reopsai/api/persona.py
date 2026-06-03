from __future__ import annotations

from urllib.parse import urlencode, urlsplit, urlunsplit

from flask import Blueprint, jsonify, redirect, request, send_file
from flask_jwt_extended import get_jwt, get_jwt_identity

from config import Config
from reopsai.application.persona_service import PersonaServiceResult, persona_service
from reopsai.shared.auth import tier_required


persona_bp = Blueprint("persona", __name__, url_prefix="/api/persona")


def _json_body():
    return request.get_json(silent=True) or {}


def _response(result: PersonaServiceResult):
    body = {"success": result.status == "ok"}
    if result.data:
        body.update(result.data)
    if result.error:
        body["error"] = result.error
    return jsonify(body), result.status_code


def _current_context():
    claims = get_jwt() or {}
    try:
        user_id = int(get_jwt_identity())
    except Exception:
        return None, (jsonify({"success": False, "error": "Invalid user identity"}), 401)
    try:
        company_id = int(claims.get("company_id"))
    except Exception:
        company_id = None
    if claims.get("account_type") != "business" or not company_id:
        return None, (jsonify({"success": False, "error": "Business company context is required"}), 403)
    return {"user_id": user_id, "company_id": company_id, "claims": claims}, None


def _require_context():
    context, error_response = _current_context()
    if error_response:
        return None, error_response
    return context, None


def _redirect_uri():
    configured = (Config.PERSONA_FIGMA_REDIRECT_URI or "").strip()
    if configured:
        parsed_configured = urlsplit(configured)
        if parsed_configured.scheme and parsed_configured.netloc and parsed_configured.path.rstrip("/") == "":
            return f"{configured.rstrip('/')}/api/persona/figma/callback"
        return configured

    backend_url = Config.BACKEND_URL.rstrip("/")
    parsed = urlsplit(backend_url)
    if parsed.scheme and parsed.netloc:
        path = parsed.path.rstrip("/")
        if path == "/api":
            path = ""
        backend_url = urlunsplit((parsed.scheme, parsed.netloc, path, "", "")).rstrip("/")
    return f"{backend_url}/api/persona/figma/callback"


def _figma_frontend_redirect(**query):
    base = f"{Config.FRONTEND_URL.rstrip('/')}{Config.PERSONA_FIGMA_REDIRECT_PATH}"
    clean = {key: value for key, value in query.items() if value is not None}
    return f"{base}?{urlencode(clean)}" if clean else base


@persona_bp.route("/folders", methods=["GET"])
@tier_required(["enterprise"])
def list_folders():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_folders(company_id=context["company_id"]))


@persona_bp.route("/folders", methods=["POST"])
@tier_required(["enterprise"])
def create_folder():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.create_folder(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/folders/<int:folder_id>", methods=["PATCH"])
@tier_required(["enterprise"])
def update_folder(folder_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.update_folder(company_id=context["company_id"], user_id=context["user_id"], folder_id=folder_id, data=_json_body()))


@persona_bp.route("/folders/<int:folder_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_folder(folder_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_folder(company_id=context["company_id"], user_id=context["user_id"], folder_id=folder_id))


@persona_bp.route("/personas", methods=["GET"])
@tier_required(["enterprise"])
def list_personas():
    context, error_response = _require_context()
    if error_response:
        return error_response
    page = max(1, int(request.args.get("page", "1")))
    limit = min(100, max(1, int(request.args.get("limit", "20"))))
    folder_id = request.args.get("folder_id") or request.args.get("folderId")
    return _response(
        persona_service.list_personas(
            company_id=context["company_id"],
            page=page,
            limit=limit,
            search=request.args.get("search"),
            folder_id=int(folder_id) if folder_id else None,
            no_folder=request.args.get("no_folder") == "true" or request.args.get("noFolder") == "true",
        )
    )


@persona_bp.route("/personas", methods=["POST"])
@tier_required(["enterprise"])
def create_persona():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.generate_personas(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/personas/segments", methods=["POST"])
@tier_required(["enterprise"])
def suggest_segments():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.suggest_segments(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/personas/manual", methods=["POST"])
@tier_required(["enterprise"])
def create_manual_persona():
    context, error_response = _require_context()
    if error_response:
        return error_response
    result = persona_service.create_persona(company_id=context["company_id"], user_id=context["user_id"], data=_json_body())
    if result.status != "ok":
        return _response(result)
    return jsonify({"success": True, "persona": result.data["data"]}), result.status_code


@persona_bp.route("/personas/save", methods=["POST"])
@tier_required(["enterprise"])
def save_personas():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.save_generated_personas(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/personas/generate", methods=["POST"])
@tier_required(["enterprise"])
def generate_personas():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.generate_personas(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/personas/<int:persona_id>", methods=["GET"])
@tier_required(["enterprise"])
def get_persona(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.get_persona(company_id=context["company_id"], persona_id=persona_id))


@persona_bp.route("/personas/<int:persona_id>", methods=["PATCH"])
@tier_required(["enterprise"])
def update_persona(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.update_persona(company_id=context["company_id"], user_id=context["user_id"], persona_id=persona_id, data=_json_body()))


@persona_bp.route("/personas/<int:persona_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_persona(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_persona(company_id=context["company_id"], user_id=context["user_id"], persona_id=persona_id))


@persona_bp.route("/personas/<int:persona_id>/memory", methods=["GET"])
@tier_required(["enterprise"])
def get_persona_memory(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.get_memory(company_id=context["company_id"], persona_id=persona_id))


@persona_bp.route("/personas/<int:persona_id>/memory/settings", methods=["PATCH"])
@persona_bp.route("/personas/<int:persona_id>/memory-settings", methods=["PATCH"])
@tier_required(["enterprise"])
def update_persona_memory_settings(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.update_memory_settings(company_id=context["company_id"], user_id=context["user_id"], persona_id=persona_id, data=_json_body()))


@persona_bp.route("/personas/<int:persona_id>/activities", methods=["POST"])
@tier_required(["enterprise"])
def add_persona_activity(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.add_activity(company_id=context["company_id"], persona_id=persona_id, data=_json_body()))


@persona_bp.route("/personas/<int:persona_id>/learned-traits", methods=["POST"])
@tier_required(["enterprise"])
def add_persona_trait(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.add_trait(company_id=context["company_id"], user_id=context["user_id"], persona_id=persona_id, data=_json_body()))


@persona_bp.route("/personas/<int:persona_id>/learned-traits/<int:trait_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_persona_trait(persona_id: int, trait_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_trait(company_id=context["company_id"], user_id=context["user_id"], persona_id=persona_id, trait_id=trait_id))


@persona_bp.route("/storage/upload", methods=["POST"])
@tier_required(["enterprise"])
def upload_persona_asset():
    context, error_response = _require_context()
    if error_response:
        return error_response
    file = request.files.get("file")
    return _response(persona_service.save_upload(company_id=context["company_id"], user_id=context["user_id"], file=file, asset_type=request.form.get("asset_type") or "upload"))


@persona_bp.route("/storage/<int:asset_id>", methods=["GET"])
@tier_required(["enterprise"])
def get_persona_asset(asset_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    result = persona_service.get_asset(company_id=context["company_id"], asset_id=asset_id)
    if result.status != "ok":
        return _response(result)
    asset = result.data["asset"]
    path = result.data["path"]
    if not path.exists():
        return jsonify({"success": False, "error": "asset file not found"}), 404
    return send_file(path, mimetype=asset.mime_type, download_name=asset.original_filename)


@persona_bp.route("/personas/<int:persona_id>/image", methods=["POST"])
@tier_required(["enterprise"])
def attach_persona_image(persona_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.attach_persona_image(company_id=context["company_id"], user_id=context["user_id"], persona_id=persona_id, data=_json_body()))


@persona_bp.route("/tests", methods=["GET"])
@tier_required(["enterprise"])
def list_ui_tests():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_ui_tests(company_id=context["company_id"]))


@persona_bp.route("/tests", methods=["POST"])
@tier_required(["enterprise"])
def create_ui_test():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.create_ui_test(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/tests/<int:test_id>", methods=["GET"])
@tier_required(["enterprise"])
def get_ui_test(test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.get_ui_test(company_id=context["company_id"], user_id=context["user_id"], test_id=test_id))


@persona_bp.route("/tests/<int:test_id>", methods=["PATCH"])
@tier_required(["enterprise"])
def update_ui_test(test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.update_ui_test(company_id=context["company_id"], user_id=context["user_id"], test_id=test_id, data=_json_body()))


@persona_bp.route("/tests/<int:test_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_ui_test(test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_ui_test(company_id=context["company_id"], user_id=context["user_id"], test_id=test_id))


@persona_bp.route("/tests/<int:test_id>/run", methods=["POST"])
@tier_required(["enterprise"])
def run_ui_test(test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.run_ui_test(company_id=context["company_id"], user_id=context["user_id"], test_id=test_id, data=_json_body()))


@persona_bp.route("/tests/<int:test_id>/results", methods=["GET"])
@tier_required(["enterprise"])
def list_ui_test_results(test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_ui_results(company_id=context["company_id"], test_id=test_id))


@persona_bp.route("/tests/capture-url", methods=["POST"])
@tier_required(["enterprise"])
def capture_url():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.capture_url(company_id=context["company_id"], user_id=context["user_id"], url=_json_body().get("url")))


@persona_bp.route("/tests/combined", methods=["GET"])
@tier_required(["enterprise"])
def list_combined_tests():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_combined_tests(company_id=context["company_id"]))


@persona_bp.route("/ab-tests", methods=["GET"])
@tier_required(["enterprise"])
def list_ab_tests():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_ab_tests(company_id=context["company_id"]))


@persona_bp.route("/ab-tests", methods=["POST"])
@tier_required(["enterprise"])
def create_ab_test():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.create_ab_test(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/ab-tests/<int:ab_test_id>", methods=["GET"])
@tier_required(["enterprise"])
def get_ab_test(ab_test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.get_ab_test(company_id=context["company_id"], ab_test_id=ab_test_id))


@persona_bp.route("/ab-tests/<int:ab_test_id>", methods=["PATCH"])
@tier_required(["enterprise"])
def update_ab_test(ab_test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.update_ab_test(company_id=context["company_id"], user_id=context["user_id"], ab_test_id=ab_test_id, data=_json_body()))


@persona_bp.route("/ab-tests/<int:ab_test_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_ab_test(ab_test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_ab_test(company_id=context["company_id"], user_id=context["user_id"], ab_test_id=ab_test_id))


@persona_bp.route("/ab-tests/<int:ab_test_id>/run", methods=["POST"])
@tier_required(["enterprise"])
def run_ab_test(ab_test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.run_ab_test(company_id=context["company_id"], user_id=context["user_id"], ab_test_id=ab_test_id, data=_json_body()))


@persona_bp.route("/ab-tests/<int:ab_test_id>/results", methods=["GET"])
@tier_required(["enterprise"])
def list_ab_test_results(ab_test_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_ab_results(company_id=context["company_id"], ab_test_id=ab_test_id))


@persona_bp.route("/interviews", methods=["GET"])
@tier_required(["enterprise"])
def list_interviews():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_interviews(company_id=context["company_id"]))


@persona_bp.route("/interviews", methods=["POST"])
@tier_required(["enterprise"])
def create_interview():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.create_interview(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/interviews/questions", methods=["POST"])
@tier_required(["enterprise"])
def generate_interview_questions():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.generate_interview_questions(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/interviews/personas", methods=["GET"])
@tier_required(["enterprise"])
def list_interview_personas():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_interview_personas(company_id=context["company_id"]))


@persona_bp.route("/interview-sources", methods=["GET"])
@tier_required(["enterprise"])
def list_interview_sources():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(
        persona_service.list_interview_sources(
            company_id=context["company_id"],
            user_id=context["user_id"],
            status=request.args.get("status"),
        )
    )


@persona_bp.route("/interview-sources", methods=["POST"])
@tier_required(["enterprise"])
def create_interview_source():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.create_interview_source(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/interview-sources/<int:source_id>", methods=["GET"])
@tier_required(["enterprise"])
def get_interview_source(source_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.get_interview_source(company_id=context["company_id"], user_id=context["user_id"], source_id=source_id))


@persona_bp.route("/interview-sources/<int:source_id>", methods=["PATCH"])
@tier_required(["enterprise"])
def update_interview_source(source_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.update_interview_source(company_id=context["company_id"], user_id=context["user_id"], source_id=source_id, data=_json_body()))


@persona_bp.route("/interview-sources/<int:source_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_interview_source(source_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_interview_source(company_id=context["company_id"], user_id=context["user_id"], source_id=source_id))


@persona_bp.route("/interview-sources/<int:source_id>/embed", methods=["POST"])
@tier_required(["enterprise"])
def embed_interview_source(source_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.embed_interview_source(company_id=context["company_id"], user_id=context["user_id"], source_id=source_id))


@persona_bp.route("/interview-sources/import-local", methods=["POST"])
@tier_required(["enterprise"])
def import_local_interview_evidence():
    context, error_response = _require_context()
    if error_response:
        return error_response
    body = _json_body()
    return _response(
        persona_service.import_local_interview_evidence(
            company_id=context["company_id"],
            user_id=context["user_id"],
            cleaning_dir=str(body.get("cleaningDir") or body.get("cleaning_dir") or "/Users/pxd/QA/cleaning"),
            embed=bool(body.get("embed", True)),
            replace_existing=bool(body.get("replaceExisting", body.get("replace_existing", True))),
        )
    )


@persona_bp.route("/interview-evidence/search", methods=["GET"])
@tier_required(["enterprise"])
def search_interview_evidence():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(
        persona_service.search_interview_evidence(
            company_id=context["company_id"],
            user_id=context["user_id"],
            target_variable=request.args.get("targetVariable") or request.args.get("target_variable") or "",
            query=request.args.get("query"),
            top_k=int(request.args.get("topK") or request.args.get("top_k") or 5),
        )
    )


@persona_bp.route("/interviews/<int:interview_id>", methods=["GET"])
@tier_required(["enterprise"])
def get_interview(interview_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.get_interview(company_id=context["company_id"], interview_id=interview_id))


@persona_bp.route("/interviews/<int:interview_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_interview(interview_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_interview(company_id=context["company_id"], user_id=context["user_id"], interview_id=interview_id))


@persona_bp.route("/interviews/<int:interview_id>/run", methods=["POST"])
@tier_required(["enterprise"])
def run_interview(interview_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.run_interview(company_id=context["company_id"], user_id=context["user_id"], interview_id=interview_id, data=_json_body()))


@persona_bp.route("/figma/status", methods=["GET"])
@tier_required(["enterprise"])
def figma_status():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.figma_status(company_id=context["company_id"], user_id=context["user_id"]))


@persona_bp.route("/figma/connect", methods=["GET"])
@tier_required(["enterprise"])
def figma_connect():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.figma_connect_url(company_id=context["company_id"], user_id=context["user_id"], redirect_uri=_redirect_uri()))


@persona_bp.route("/figma/callback", methods=["GET"])
@tier_required(["enterprise"])
def figma_callback():
    context, error_response = _require_context()
    if error_response:
        return error_response
    code = request.args.get("code")
    if not code:
        return redirect(_figma_frontend_redirect(connected=0, error="missing_code"))
    result = persona_service.figma_callback(company_id=context["company_id"], user_id=context["user_id"], code=code, redirect_uri=_redirect_uri())
    if result.status != "ok":
        return redirect(_figma_frontend_redirect(connected=0, error=result.error))
    return redirect(_figma_frontend_redirect(connected=1))


@persona_bp.route("/figma/disconnect", methods=["DELETE"])
@tier_required(["enterprise"])
def figma_disconnect():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.figma_disconnect(company_id=context["company_id"], user_id=context["user_id"]))


@persona_bp.route("/figma/files", methods=["GET"])
@tier_required(["enterprise"])
def list_figma_files():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_figma_files(company_id=context["company_id"], user_id=context["user_id"]))


@persona_bp.route("/figma/files/sync", methods=["POST"])
@tier_required(["enterprise"])
def sync_figma_file():
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.sync_figma_file(company_id=context["company_id"], user_id=context["user_id"], data=_json_body()))


@persona_bp.route("/figma/files/<int:file_id>/sync", methods=["POST"])
@tier_required(["enterprise"])
def refresh_figma_file(file_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.refresh_figma_file(company_id=context["company_id"], user_id=context["user_id"], file_id=file_id))


@persona_bp.route("/figma/files/<int:file_id>", methods=["DELETE"])
@tier_required(["enterprise"])
def delete_figma_file(file_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.delete_figma_file(company_id=context["company_id"], file_id=file_id))


@persona_bp.route("/figma/files/<int:file_id>/flows", methods=["GET"])
@tier_required(["enterprise"])
def list_figma_flows(file_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.list_figma_flows(company_id=context["company_id"], file_id=file_id))


@persona_bp.route("/figma/files/<int:file_id>/flows/<int:flow_id>/preview", methods=["GET"])
@tier_required(["enterprise"])
def preview_figma_flow(file_id: int, flow_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.preview_figma_flow(company_id=context["company_id"], user_id=context["user_id"], file_id=file_id, flow_id=flow_id))


@persona_bp.route("/figma/files/<int:file_id>/flows/sync", methods=["POST"])
@tier_required(["enterprise"])
def sync_figma_flows(file_id: int):
    context, error_response = _require_context()
    if error_response:
        return error_response
    return _response(persona_service.sync_figma_flows(company_id=context["company_id"], file_id=file_id, data=_json_body()))
