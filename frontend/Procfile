# Single worker is REQUIRED: SSE subscribers, the status watchdog, and active
# response state live in-process and are not shared across workers/dynos.
# Long-lived SSE connections each hold a thread, so we use threaded workers.
web: gunicorn --chdir ui_template_multi_agent_chatbot app:app --workers 1 --threads 16 --timeout 120 --bind 0.0.0.0:$PORT
