import hashlib
import os
import re
import warnings
import zipfile
from io import BytesIO
from pathlib import Path
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from PIL import Image, ImageFile, ImageOps, UnidentifiedImageError

# Supplier feeds contain broken TIFFs surprisingly often.
ImageFile.LOAD_TRUNCATED_IMAGES = True

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="PIL.TiffImagePlugin",
)

# Must be the first Streamlit call in the script.
st.set_page_config(
    page_title="Image Downloader",
    layout="wide",
)

REQUEST_TIMEOUT = 20
MAX_DOWNLOAD_BYTES = 80 * 1024 * 1024
MAX_DIMENSION = 4000

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

# Prevent decompression bombs and giant memory spikes.
Image.MAX_IMAGE_PIXELS = 80_000_000

OUTPUT_DIR = Path("output")
OUTPUT_DIR.mkdir(exist_ok=True)

JOB_RE = re.compile(r"[0-9a-f]{12}")


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------
def safe_filename(value):
    value = str(value).strip()
    return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", value)


def is_valid_url(url):
    try:
        parsed = urlparse(str(url))
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)
    except Exception:
        return False


def flatten_transparency(image):
    background = Image.new("RGB", image.size, (255, 255, 255))

    if image.mode in ("RGBA", "LA"):
        background.paste(image, mask=image.getchannel("A"))
    else:
        background.paste(image)

    return background


def prepare_image(image, source_format):
    # The format must be read BEFORE exif_transpose(): the transposed copy
    # has image.format == None, which made every file fall back to JPG.
    fmt = (source_format or "").lower()

    if fmt in ("png", "webp", "avif"):
        ext = "png"
    else:
        ext = "jpg"

    if image.mode in ("RGBA", "LA"):
        image = flatten_transparency(image)
    elif image.mode == "P":
        image = flatten_transparency(image.convert("RGBA"))
    elif image.mode != "RGB":
        image = image.convert("RGB")

    # Large TIFFs from DAM systems are usually absurdly oversized.
    if image.width > MAX_DIMENSION or image.height > MAX_DIMENSION:
        image.thumbnail((MAX_DIMENSION, MAX_DIMENSION))

    return image, ext


def download_image(url):
    response = None

    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            stream=True,
            allow_redirects=True,
        )
        response.raise_for_status()

        content_length = response.headers.get("Content-Length")
        if content_length and content_length.isdigit():
            if int(content_length) > MAX_DOWNLOAD_BYTES:
                return None, None, "File too large"

        # Stream in chunks and enforce the size limit while reading.
        raw = BytesIO()
        size = 0
        for chunk in response.iter_content(chunk_size=256 * 1024):
            size += len(chunk)
            if size > MAX_DOWNLOAD_BYTES:
                return None, None, "File too large"
            raw.write(chunk)

        if size == 0:
            return None, None, "Empty response"

        raw.seek(0)
        image = Image.open(raw)
        source_format = image.format

        # EXIF rotation before conversion avoids weird orientation bugs.
        image = ImageOps.exif_transpose(image)
        image, ext = prepare_image(image, source_format)

        buffer = BytesIO()
        if ext == "jpg":
            image.save(buffer, format="JPEG", quality=90, optimize=True)
        else:
            image.save(buffer, format="PNG", optimize=True)

        image.close()
        return buffer.getvalue(), ext, None

    except UnidentifiedImageError:
        return None, None, "Unsupported image"
    except requests.exceptions.RequestException as e:
        return None, None, str(e)
    except OSError as e:
        # Pillow raises OSError on damaged TIFFs.
        return None, None, str(e)
    except Exception as e:
        return None, None, str(e)
    finally:
        if response is not None:
            response.close()


def detect_product_column(columns):
    candidates = ["name", "product_name", "product", "item", "title"]

    for col in columns:
        lowered = str(col).lower()
        if any(x in lowered for x in candidates):
            return col

    return columns[0]


def already_downloaded(images_dir, filename):
    return any(
        (images_dir / f"{filename}.{ext}").exists() for ext in ("jpg", "png")
    )


# --------------------------------------------------------------------------
# Main page
# --------------------------------------------------------------------------
st.title("Image Downloader")
st.write("Upload CSV → download images → ZIP export")

uploaded_file = st.file_uploader("Upload CSV", type=["csv"])

if uploaded_file is not None:
    csv_bytes = uploaded_file.getvalue()

    try:
        # dtype=str + keep_default_na=False: empty cells stay "" instead of
        # becoming the string "nan" (which was reported as "Invalid URL").
        try:
            df = pd.read_csv(
                BytesIO(csv_bytes),
                dtype=str,
                keep_default_na=False,
                encoding="utf-8-sig",
            )
        except UnicodeDecodeError:
            df = pd.read_csv(
                BytesIO(csv_bytes),
                dtype=str,
                keep_default_na=False,
                encoding="latin-1",
            )
    except Exception as e:
        st.error(f"CSV error: {e}")
        st.stop()

    if df.empty or len(df.columns) < 2:
        st.error("CSV needs a product column and at least one image URL column.")
        st.stop()

    df = df.apply(
        lambda s: s.str.strip().str.replace("\u200b", "", regex=False)
    )

    product_column = detect_product_column(list(df.columns))
    st.success(f"Using product column: {product_column}")

    # Same CSV content -> same job -> resumes after a crash
    job_id = hashlib.sha1(csv_bytes).hexdigest()[:12]

    images_dir = OUTPUT_DIR / job_id / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    zip_path = OUTPUT_DIR / f"images_{job_id}.zip"

    if zip_path.exists():
        st.info("This CSV was already processed. Click Start / resume to fetch missing images.")

    if st.button("Start / resume download"):
        progress = st.progress(0)
        status = st.empty()

        total_urls = downloaded = skipped = resumed = 0
        error_rows = []
        total_rows = len(df)
        name_counts = {}

        for row_index, (_, row) in enumerate(df.iterrows()):
            # Duplicate product names get a stable suffix (deterministic for a
            # given CSV, so resume still works) instead of overwriting.
            product_name = safe_filename(row[product_column]) or f"row_{row_index}"
            dup = name_counts.get(product_name, 0)  # 0 = first occurrence
            name_counts[product_name] = dup + 1

            image_index = 0

            for col in df.columns:
                if col == product_column:
                    continue

                image_url = row[col]
                if not image_url:
                    continue

                total_urls += 1

                # Index = position among the row's URLs, not number of
                # successes, so a retried failure never shifts file names.
                # First occurrence:  name, name_1, name_2 ...
                # Duplicates:        name_<dup>_1, name_<dup>_2 ...
                if dup == 0:
                    filename = (
                        product_name
                        if image_index == 0
                        else f"{product_name}_{image_index}"
                    )
                else:
                    filename = f"{product_name}_{dup}_{image_index + 1}"
                image_index += 1

                if already_downloaded(images_dir, filename):
                    resumed += 1
                    continue

                if not is_valid_url(image_url):
                    skipped += 1
                    error_rows.append(
                        {"row": row_index, "url": image_url, "error": "Invalid URL"}
                    )
                    continue

                status.text(
                    f"Downloaded: {downloaded} | Resumed: {resumed} | Skipped: {skipped}"
                )

                image_bytes, ext, error = download_image(image_url)
                if error:
                    skipped += 1
                    error_rows.append(
                        {"row": row_index, "url": image_url, "error": error}
                    )
                    continue

                # Write to .part first, then rename: a crash never leaves a
                # half-written image. (with_name, not with_suffix, so names
                # containing dots are handled correctly.)
                final_path = images_dir / f"{filename}.{ext}"
                tmp_path = final_path.with_name(final_path.name + ".part")
                tmp_path.write_bytes(image_bytes)
                tmp_path.replace(final_path)

                downloaded += 1

            progress.progress((row_index + 1) / total_rows)

        # Build the ZIP from disk at the end
        tmp_zip = zip_path.with_name(zip_path.name + ".part")
        with zipfile.ZipFile(tmp_zip, "w", compression=zipfile.ZIP_STORED) as zipf:
            for p in sorted(images_dir.iterdir()):
                if p.suffix in (".jpg", ".png"):
                    zipf.write(p, arcname=p.name)
        tmp_zip.replace(zip_path)

        st.session_state["last_stats"] = {
            "job": job_id,
            "found": total_urls,
            "downloaded": downloaded,
            "resumed": resumed,
            "skipped": skipped,
            "errors": error_rows,
        }
        st.rerun()

    # Rendered on every run, outside the button block
    stats = st.session_state.get("last_stats")
    if stats and stats.get("job") == job_id:
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("URLs found", stats["found"])
        c2.metric("Downloaded", stats["downloaded"])
        c3.metric("Resumed", stats["resumed"])
        c4.metric("Skipped", stats["skipped"])

        if stats["errors"]:
            with st.expander("Errors"):
                st.dataframe(pd.DataFrame(stats["errors"]), width="stretch")
