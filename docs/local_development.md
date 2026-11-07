# Run the hello world app

The current app has one route: FastAPI serves `src/frontend/index.html` at `/`.
There is one Python process; no frontend build or separate frontend server is needed.

## First-time setup

From the repository root, using Python 3.11 or newer:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r src/backend/requirements.txt
```

No AWS credentials, API keys, or `.env` entries are required for this step.

## Run in VS Code

1. Open the repository folder in VS Code.
2. Install Microsoft's **Python** and **Python Debugger** extensions if needed.
3. Open **Run and Debug**, select **run full stack app**, and press **F5**.
4. Open <http://127.0.0.1:8000/>. The page should display **Hello world!**
5. Stop the server with **Shift+F5**.

The launch configuration uses the repository's `.venv` directly. Set a breakpoint
in `src/backend/main.py` and refresh the page to debug the request.

## Run from a terminal

From the repository root:

```bash
.venv/bin/python -m uvicorn src.backend.main:app --host 127.0.0.1 --port 8000 --reload --reload-dir src/backend
```

Stop the terminal server with **Ctrl+C**. Run either this command or the VS Code
configuration at a time; both use port 8000.

Python changes restart the server automatically. After editing
`src/frontend/index.html`, refresh the browser to see the updated page; the file
is read on each request.

## Verify

While the server is running, use another terminal:

```bash
curl -i http://127.0.0.1:8000/
```

Expect `200 OK`, a `text/html` content type, and the hello world HTML.
Other paths return `404`; API documentation endpoints are disabled for this
single-page boilerplate.

## Deployment status

Docker packaging and a TypeScript CDK stack for **us-east-1** are now available.
See [Docker and AWS deployment](aws_deployment.md) for container testing,
infrastructure setup, and the one-command release flow. Stop the Docker app
before using VS Code; both use local port 8000.

## References

- [FastAPI file responses](https://fastapi.tiangolo.com/advanced/custom-response/#fileresponse)
- [Uvicorn settings](https://uvicorn.dev/settings/)
