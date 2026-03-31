import streamlit as st
import pandas as pd
import requests
import re
import zipfile
import tempfile
from io import BytesIO
from urllib.parse import urlparse
from PIL import Image, ImageFile

# allows loading imperfect JPGs (VERY IMPORTANT)
ImageFile.LOAD_TRUNCATED_IMAGES = True

REQUEST_TIMEOUT = 25
MAX_WORKERS = 6


# -------------------------------------------------
# HELPERS
# -------------------------------------------------

def safe_filename(name):
    return re.sub(r'[<>:"/\\|?*\n\r\t]', "_", str(name).strip())


def is_valid_url(url):
    try:
        p = urlparse(str(url))
        return p.scheme in ("http", "https") and p.netloc != ""
    except:
        return False


def detect_product_column(df):
    keys = ["name", "product", "title", "item"]
    for col in df.columns:
        if any(k in col.lower() for k in keys):
            return col
    return df.columns[0]


# -------------------------------------------------
# IMAGE FORMAT DETECTION (bulletproof)
# -------------------------------------------------

def detect_extension(data, pil_format):
    if pil_format:
        pil_format = pil_format.upper()

    if pil_format in ["JPEG", "JPG"]:
        return "jpg"
    if pil_format == "PNG":
        return "png"
    if pil_format == "WEBP":
        return "webp"
    if pil_format == "GIF":
        return "gif"

    # fallback magic bytes
    if data.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if data.startswith(b"\x89PNG"):
        return "png"
    if data[0:4] == b"RIFF" and b"WEBP" in data[:16]:
        return "webp"
    if data[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"

    return None


# -------------------------------------------------
# DOWNLOAD IMAGE (CORE FIX)
# -------------------------------------------------

def download_image(url):
    r = requests.get(
        url,
        timeout=REQUEST_TIMEOUT,
        headers={"User-Agent": "Mozilla/5.0"},
    )
    r.raise_for_status()

    data = r.content

    if len(data) < 100:
        raise Exception("File too small — not image")

    pil_format = None

    try:
        img = Image.open(BytesIO(data))
        img.load()
        pil_format = img.format

        # normalize problematic JPG alpha
        if pil_format in ["JPEG", "JPG"] and img.mode in ("RGBA", "LA"):
            img = img.convert("RGB")
            buf = BytesIO()
            img.save(buf, format="JPEG", quality=95)
            data = buf.getvalue()

    except Exception:
        # Pillow failed → still try magic-byte detection
        pass

    ext = detect_extension(data, pil_format)

    if not ext:
        raise Exception("Unsupported image format")

    return data, ext


# -------------------------------------------------
# STREAMLIT UI
# -------------------------------------------------

st.title("📦 CSV Image Downloader (API + JPG + PNG + WEBP FIXED)")

uploaded = st.file_uploader("Upload CSV", type=["csv"])

if uploaded:

    df = pd.read_csv(uploaded)
    df = df.applymap(lambda x: str(x).strip() if isinstance(x, str) else x)

    st.success(f"Loaded {len(df)} rows")

    product_column = detect_product_column(df)
    st.info(f"Detected product column: {product_column}")

    if st.button("🚀 Start"):

        progress = st.progress(0)
        status = st.empty()

        downloaded = 0
        skipped = 0
        errors = 0
        error_log = []

        temp_dir = tempfile.mkdtemp()
        zip_path = f"{temp_dir}/images.zip"

        total_rows = len(df)
        processed_rows = 0

        with zipfile.ZipFile(
            zip_path,
            "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=5,
        ) as zipf:

            for idx, row in df.iterrows():

                product_name = safe_filename(row[product_column])
                img_index = 0

                for col in df.columns:

                    if col == product_column:
                        continue

                    url = row[col]

                    if pd.isnull(url) or not url:
                        continue

                    if not is_valid_url(url):
                        skipped += 1
                        error_log.append(
                            {"row": idx, "url": url, "reason": "Invalid URL"}
                        )
                        continue

                    try:
                        data, ext = download_image(url)

                        name = (
                            f"{product_name}_{img_index}.{ext}"
                            if img_index > 0
                            else f"{product_name}.{ext}"
                        )

                        zipf.writestr(name, data)

                        downloaded += 1
                        img_index += 1

                    except Exception as e:
                        errors += 1
                        error_log.append(
                            {"row": idx, "url": url, "reason": str(e)}
                        )

                processed_rows += 1
                progress.progress(processed_rows / total_rows)

                status.text(
                    f"Rows {processed_rows}/{total_rows} | "
                    f"Downloaded {downloaded} | Errors {errors}"
                )

        # read zip safely (FIXES EMPTY ARCHIVE)
        with open(zip_path, "rb") as f:
            zip_bytes = f.read()

        # -------------------------------------------------
        # SUMMARY
        # -------------------------------------------------

        st.subheader("Summary")

        c1, c2, c3 = st.columns(3)
        c1.metric("Downloaded", downloaded)
        c2.metric("Skipped", skipped)
        c3.metric("Errors", errors)

        if error_log:
            err_df = pd.DataFrame(error_log)
            st.dataframe(err_df, use_container_width=True)

            st.download_button(
                "Download error report",
                err_df.to_csv(index=False).encode(),
                "errors.csv",
                "text/csv",
            )

        st.success("ZIP ready")

        st.download_button(
            "⬇️ Download images.zip",
            zip_bytes,
            "images.zip",
            "application/zip",
        )
