import asyncio
import os
import shutil
import aiofiles
from fastapi import FastAPI, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from iiif_download import IIIFManifest
import uuid
import img2pdf
import ocrmypdf
from pathlib import Path
from starlette.background import BackgroundTask


async def async_log_monitor(events_log):
    async with aiofiles.open(events_log, mode='r') as f:
        while True:
            line = await f.readline()
            if not line:
                await asyncio.sleep(0.1)  # Non-blocking sleep
                # print("Nothing new")
            elif line.startswith("END---"):
                print("End of log. Stopping monitoring.")
                break
            else:
                yield f"data: {line.strip()}\n\n"

app = FastAPI()

origins = [
    "http://iiif-downloader.liamengland.com",
    "http://localhost",
    "http://localhost:5173"
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_headers=["*"],
)


@app.get("/iiif")
async def iiif_endpoint(manifestURL: str, response: Response, ocr: bool = True, pctSize: float = 0.35):
    # Load the IIIF manifest
    # manifest_url = "https://dlg.usg.edu/record/guan_1633_040-017/presentation/manifest.json"
    # Long document
    # manifest_url = "https://dlg.usg.edu/record/guan_1633_088-017/presentation/manifest.json"
    # http://localhost:8000/iiif?manifestURL=https%3A%2F%2Fdlg.usg.edu%2Frecord%2Fguan_1633_040-017%2Fpresentation%2Fmanifest.json
    manifest_url = manifestURL
    if not manifest_url or not manifest_url.startswith("http") or "manifest" not in manifest_url:
        response.status_code = 400
        return {"error": "Invalid manifest URL"}
    manifest = IIIFManifest(manifest_url, pct_size=pctSize)
    temp_dir = f"{str(uuid.uuid4())}/"
    # Download the manifest
    # await manifest.load()
    # Return the manifest data
    await manifest.download(temp_dir)

    # https://gitlab.mister-muffin.de/josch/img2pdf
    temp_dir = Path("img").joinpath(temp_dir)
    pdf_dir = temp_dir.joinpath("pdf")
    pdf_dir.mkdir(parents=True, exist_ok=True)
    image_files = sorted(temp_dir.glob("*.jpg"))
    with open(pdf_dir.joinpath("out.pdf"), "wb") as f:
        f.write(img2pdf.convert([str(img) for img in image_files]))

    if ocr:
        # OCR the PDF
        ocrmypdf.ocr(
            pdf_dir.joinpath("out.pdf"),
            pdf_dir.joinpath("out_ocr.pdf"),
            language="eng",
            deskew=False,
            rotate_pages=False,
            output_type="pdf",
            optimize=0
        )
        # Return the PDF file
        return FileResponse(pdf_dir.joinpath("out_ocr.pdf"), media_type="application/pdf")
    else:
        # Return the PDF file
        return FileResponse(pdf_dir.joinpath("out.pdf"), media_type="application/pdf")


@app.get("/iiif2")
async def iiif_endpoint_eventstream(manifestURL: str, response: Response, ocr: bool = True, pctSize: float = 0.35):
    manifest_url = manifestURL
    if not manifest_url or not manifest_url.startswith("http") or "manifest" not in manifest_url:
        response.status_code = 400
        response.headers["Content-Type"] = "application/json"
        return {"error": "Invalid manifest URL"}

    async def event_stream():
        # Step 1: Download images from the IIIF manifest

        manifest = IIIFManifest(manifest_url, pct_size=pctSize, is_logged=False)
        temp_dir = f"{str(uuid.uuid4())}/"
        events_log = Path("img").joinpath(temp_dir, "events.log")
        events_log.parent.mkdir(parents=True, exist_ok=True)
        events_log.touch()

        download_task = asyncio.create_task(manifest.download(temp_dir))

        async for message in async_log_monitor(events_log):
            yield message

        # yield "data: Downloading images...\n\n"

        await download_task

        # yield "data: Downloading images... done.\n\n"

        # Step 2: Convert images to PDF
        yield "data: Converting images to PDF...\n\n"
        temp_dir = Path("img").joinpath(temp_dir)
        pdf_dir = temp_dir.joinpath("pdf")
        pdf_dir.mkdir(parents=True, exist_ok=True)
        image_files = sorted(temp_dir.glob("*.jpg"))
        # with open(pdf_dir.joinpath("out.pdf"), "wb") as f:
        #    f.write(img2pdf.convert([str(img) for img in image_files]))
        await asyncio.to_thread(
            lambda: pdf_dir.joinpath("out.pdf").write_bytes(
                img2pdf.convert([str(img) for img in image_files])
            )
        )
        yield "data: PDF created successfully.\n\n"

        # Step 3: Perform OCR if requested
        if ocr:
            yield "data: Running OCR on the PDF... this can take a while. Please be patient!\n\n"
            await asyncio.to_thread(
                ocrmypdf.ocr,
                pdf_dir.joinpath("out.pdf"),
                pdf_dir.joinpath("out_ocr.pdf"),
                language="eng",
                deskew=False,
                rotate_pages=False,
                output_type="pdf",
                optimize=0
            )
            yield "data: OCR completed successfully.\n\n"
            yield f"data:pdfurl:/tmp/{str(pdf_dir.joinpath('out_ocr.pdf')).replace('img/', '')}\n\n"
        else:
            yield f"data:pdfurl:/tmp/{str(pdf_dir.joinpath('out.pdf')).replace('img/', '')}\n\n"

        yield "data:close\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.get("/tmp/{file_path:path}")
async def get_pdf(file_path: str, response: Response):
    file_location = Path("img").joinpath(file_path)
    if not file_location.exists():
        response.status_code = 404
        response.headers["Content-Type"] = "application/json"
        return {"error": "File not found"}
    return FileResponse(file_location, media_type="application/pdf", background=BackgroundTask(cleanup, file_location))


def cleanup(path: Path):
    target_dir = path.parent.parent
    if target_dir.exists():
        shutil.rmtree(target_dir)
        print(f"\n\n{target_dir} removed")
    else:
        os.remove(path)
