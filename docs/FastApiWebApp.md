# FastApiWebAppPlugin — Endpoint Usage & Patterns

This document explains how the **FastApiWebAppPlugin** auto-generates FastAPI endpoints for your plugin methods, including best practices for upload/download, request patterns, file handling, and example usage.

## Table of Contents

- [Overview](#overview)
- [How Endpoints Are Generated](#how-endpoints-are-generated)
- [Example Endpoints](#example-endpoints)
  - [1. Simple (Non-Streaming) Endpoints](#1-simple-non-streaming-endpoints)
  - [2. File Uploads (streaming_type="upload")](#2-file-uploads-streaming_typeupload)
  - [3. File Downloads (streaming_type="download")](#3-file-downloads-streaming_typedownload)
- [Client Examples — cURL](#client-examples--curl)
- [Best Practices](#best-practices)
- [Further References](#further-references)

## Overview

- **FastApiWebAppPlugin** exposes Python class methods (with `@endpoint`) as HTTP endpoints.
- Supports:
  - Regular GET/POST endpoints (with JSON or query args)
  - Efficient, scalable file uploads (chunked saving, any metadata via form)
  - File downloads (plugin returns a file path, served as streaming download)

## How Endpoints Are Generated

- Add `@BasePlugin.endpoint(...)` to your class methods.
    - Set `streaming_type="upload"` to make an upload endpoint
    - Set `streaming_type="download"` for a streaming download
- For uploads:
    - Files are accepted using `multipart/form-data`, streamed directly to a temp file in `/tmp/<random>` to avoid RAM bottlenecks
    - Additional client form fields (e.g., "secret") are available as a Python dict
    - After save, the file path (and other variables) are passed to your plugin method

## Example Endpoints

### 1. Simple (Non-Streaming) Endpoints

#### Plugin
```
@BasePlugin.endpoint(method="post")
def add_data(self, value: str):
    """Add new data."""
    return {"status": "success", "stored": value}
```
#### cURL
```
curl -X POST -H "Content-Type: application/json" \
     -d '{"value": "hello"}' \
     http://localhost:8000/add_data
```

### 2. File Uploads (`streaming_type="upload"`)

- Upload a file + additional properties via multipart/form-data
- File automatically saved to `/tmp`, sent to plugin as file path

#### Plugin
```
@BasePlugin.endpoint(method="post", streaming_type="upload")
def add_file(self, file_path: str, body):
    """
    Accepts a file saved at file_path and form fields (body dict).
    """
    secret = body.get('secret')
    # process the file, use 'secret', etc.
    ...
```

#### Generated Endpoint
```
@app.post("/add_file")
async def add_file_upload(
    file: UploadFile = File(...),
    secret: str = Form(None)
):
    # File is saved in /tmp/upload_<random>/<uuid>_<filename>
    ...
    result = await eng.call_plugin("add_file", file_path, {"secret": secret})
    return result
```

#### cURL
```
curl -X POST \
  -F "file=@/path/to/myfile.dat" \
  -F "secret=my_secret_value" \
  http://localhost:8000/add_file
```

### 3. File Downloads (`streaming_type="download"`)

- Plugin returns file path.
- Endpoint streams file to client.

#### Plugin
```
@BasePlugin.endpoint(method="get", streaming_type="download")
def download_file(self, cid: str):
    """Returns file path by content id."""
    return self.r1fs.get_file(cid=cid)
```

#### cURL
```
curl -O -J \
     "http://localhost:8000/download_file?cid=Qm123abc"
```

## Client Examples — cURL

- **Upload:**  
  ```
  curl -X POST \
    -F "file=@myphoto.jpg" -F "secret=swordfish" \
    http://localhost:8000/add_file
  ```

- **Download:**  
  ```
  curl -O -J \
    "http://localhost:8000/download_file?cid=Qm123..."
  ```

- **Status:**  
  ```
  curl http://localhost:8000/get_status
  ```

## Best Practices

- **Temp Files**: Your plugin can/should cleanup temp files/folders after processing.
- **Body Fields**: Any extra form fields (like `secret`) arrive in a dict (`body`) for convenient use.
- **Filename**: Original filename is preserved in saved file via a UUID+filename scheme for traceability.
- **No RAM bottleneck**: Even large files never fill server memory.
- **Error Handling**: Validate and return structured errors in your plugin.

## Further References

- [FastAPI File Uploads Docs](https://fastapi.tiangolo.com/tutorial/request-files/)
- [FastAPI StreamingResponse](https://fastapi.tiangolo.com/advanced/custom-response/#streamingresponse)
- [Multipart Forms in FastAPI](https://fastapi.tiangolo.com/tutorial/request-forms/)
- [cURL with file upload](https://curl.se/docs/manual.html#-F)


**For template developers and contributors:**  
See top of `naeural_core/business/base/uvicorn_templates/basic_server.j2` for a cross-reference, and keep this doc up to date if endpoint generation logic changes.

*Contact project maintainers for more advanced integration guidance or if you want to extend the template and plugin mechanics for new use cases.*
```