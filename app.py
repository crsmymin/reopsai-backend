"""Compatibility entrypoint for Flask and ASGI deployments."""

from config import Config
from reopsai.api.app_factory import create_app


app = create_app()


if __name__ == "__main__":
    try:
        from services.vector_service import vector_service
    except Exception:
        vector_service = None

    if vector_service is None:
        print("=" * 50)
        print("경고: Vector DB 서비스가 초기화되지 않았습니다.")
        print("앱이 정상 작동하지 않을 수 있습니다.")
        print("터미널에서 'python vector_db_service.py'를 먼저 실행하여 DB를 구축하세요.")
        print("=" * 50)

    print(f"Starting Flask server on port {Config.PORT}...")
    app.run(debug=Config.DEBUG, port=Config.PORT, host="0.0.0.0")
