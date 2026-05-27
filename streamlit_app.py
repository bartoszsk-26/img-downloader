import os
import re
import zipfile
import tempfile
import warnings

from io import BytesIO
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st

from PIL import (
    Image,
    ImageFile,
    ImageOps,
    UnidentifiedImageError,
)
from PIL import ImageFile

ImageFile.LOAD_TRUNCATED_IMAGES = True

# Supplier feeds contain broken TIFFs surprisingly often.
ImageFile.LOAD_TRUNCATED_IMAGES = True

warnings.filterwarnings(
    "ignore",
    category=UserWarning,
    module="PIL.TiffImagePlugin"
)

st.set_page_config(
    page_title="Image Downloader",
    layout="wide",
)

st.title("Image Downloader")
st.write("Upload CSV → download images → ZIP export")

REQUEST_TIMEOUT = 20

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}

# Prevent decompression bombs and giant memory spikes.
MAX_IMAGE_PIXELS = 80_000_000

Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS


def safe_filename(value):
    value = str(value).strip()

    return re.sub(
        r'[<>:"/\\|?*\n\r\t]',
        "_",
        value
    )


def is_valid_url(url):
    try:
        parsed = urlparse(str(url))

        return (
            parsed.scheme in ("http", "https")
            and parsed.netloc
        )

    except Exception:
        return False


def flatten_transparency(image):
    background = Image.new(
        "RGB",
        image.size,
        (255, 255, 255)
    )

    if image.mode in ("RGBA", "LA"):
        alpha = image.getchannel("A")
        background.paste(image, mask=alpha)

    else:
        background.paste(image)

    return background


def prepare_image(image):
    fmt = (image.format or "").lower()

    if fmt in ("jpeg", "jpg"):
        ext = "jpg"

    elif fmt in ("png", "webp", "avif"):
        ext = "png"

    elif fmt in ("tiff", "tif"):
        ext = "jpg"

    else:
        ext = "jpg"

    if image.mode in ("RGBA", "LA"):
        image = flatten_transparency(image)

    elif image.mode == "P":
        image = flatten_transparency(
            image.convert("RGBA")
        )

    elif image.mode != "RGB":
        image = image.convert("RGB")

    # Large TIFFs from DAM systems are usually absurdly oversized.
    max_dimension = 4000

    if (
        image.width > max_dimension
        or image.height > max_dimension
    ):
        image.thumbnail(
            (max_dimension, max_dimension)
        )

    return image, ext


def download_image(url):
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

        # Skip absurd files before loading into memory.
        if content_length:
            if int(content_length) > 80 * 1024 * 1024:
                return None, None, "File too large"

        raw = response.content

        if not raw:
            return None, None, "Empty response"

        image = Image.open(BytesIO(raw))

        # EXIF rotation before conversion avoids weird orientation bugs.
        image = ImageOps.exif_transpose(image)

        image, ext = prepare_image(image)

        buffer = BytesIO()

        if ext == "jpg":
            image.save(
                buffer,
                format="JPEG",
                quality=90,
                optimize=True,
            )

        else:
            image.save(
                buffer,
                format="PNG",
                optimize=True,
            )

        buffer.seek(0)

        return buffer.read(), ext, None

    except UnidentifiedImageError:
        return None, None, "Unsupported image"

    except requests.exceptions.RequestException as e:
        return None, None, str(e)

    except OSError as e:
        # Pillow throws OSError on damaged TIFFs.
        return None, None, str(e)

    except Exception as e:
        return None, None, str(e)


def detect_product_column(columns):
    candidates = [
        "name",
        "product_name",
        "product",
        "item",
        "title",
    ]

    for col in columns:
        lowered = col.lower()

        if any(x in lowered for x in candidates):
            return col

    return columns[0]


uploaded_file = st.file_uploader(
    "Upload CSV",
    type=["csv"]
)

if uploaded_file is not None:

    try:
        df = pd.read_csv(uploaded_file)

    except Exception as e:
        st.error(f"CSV error: {e}")
        st.stop()

    for col in df.select_dtypes(include=["object", "string"]):
        df[col] = (
            df[col]
            .astype(str)
            .str.strip()
            .str.replace("\u200b", "", regex=False)
        )

    product_column = detect_product_column(df.columns)

    st.success(f"Using product column: {product_column}")

    if st.button("Start download"):

        progress = st.progress(0)
        status = st.empty()

        total_urls = 0
        downloaded = 0
        skipped = 0

        error_rows = []

        with tempfile.TemporaryDirectory() as temp_dir:

            zip_path = os.path.join(
                temp_dir,
                "images.zip"
            )

            with zipfile.ZipFile(
                zip_path,
                "w",
                compression=zipfile.ZIP_DEFLATED,
            ) as zipf:

                total_rows = len(df)

                for row_index, (_, row) in enumerate(df.iterrows()):

                    product_name = safe_filename(
                        row[product_column]
                    )

                    image_index = 0

                    for col in df.columns:

                        if col == product_column:
                            continue

                        image_url = row[col]

                        if pd.isna(image_url):
                            continue

                        image_url = str(image_url).strip()

                        if not image_url:
                            continue

                        total_urls += 1

                        if not is_valid_url(image_url):
                            skipped += 1

                            error_rows.append({
                                "row": row_index,
                                "url": image_url,
                                "error": "Invalid URL",
                            })

                            continue

                        status.text(
                            f"Downloaded: {downloaded} | Skipped: {skipped}"
                        )

                        image_bytes, ext, error = download_image(image_url)

                        if error:
                            skipped += 1

                            error_rows.append({
                                "row": row_index,
                                "url": image_url,
                                "error": error,
                            })

                            continue

                        filename = (
                            f"{product_name}_{image_index}"
                            if image_index > 0
                            else product_name
                        )

                        temp_image_path = os.path.join(
                            temp_dir,
                            f"{filename}.{ext}"
                        )

                        with open(temp_image_path, "wb") as f:
                            f.write(image_bytes)

                        zipf.write(
                            temp_image_path,
                            arcname=f"{filename}.{ext}"
                        )

                        downloaded += 1
                        image_index += 1

                    progress.progress(
                        (row_index + 1) / total_rows
                    )

            st.success("Finished")

            c1, c2, c3 = st.columns(3)

            c1.metric("URLs found", total_urls)
            c2.metric("Downloaded", downloaded)
            c3.metric("Skipped", skipped)

            if error_rows:
                with st.expander("Errors"):

                    error_df = pd.DataFrame(error_rows)

                    st.dataframe(
                        error_df,
                        width="stretch",
                    )

            with open(zip_path, "rb") as f:
                st.download_button(
                    "Download ZIP",
                    data=f,
                    file_name="images.zip",
                    mime="application/zip",
                )
