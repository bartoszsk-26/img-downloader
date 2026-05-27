import os
import re
import zipfile
import tempfile
from io import BytesIO
from urllib.parse import urlparse

import pandas as pd
import requests
import streamlit as st
from PIL import Image, ImageOps, UnidentifiedImageError

st.set_page_config(
    page_title="Image Downloader",
    layout="wide"
)

st.title("Image Downloader")
st.write("Upload CSV → download images → ZIP export")

REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": "Mozilla/5.0",
    "Accept": "*/*",
}


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


def prepare_image(image, fmt):
    fmt = (fmt or "").lower()

    if fmt in ("jpeg", "jpg"):
        ext = "jpg"

    elif fmt in ("png", "webp", "avif"):
        # PNG output avoids weird alpha artifacts from vendor assets
        ext = "png"

    elif fmt in ("tiff", "tif"):
        ext = "jpg"

    else:
        ext = "jpg"

    # Vendor feeds often contain transparent packshots.
    # White background looks better for marketplaces and PDFs.
    if image.mode in ("RGBA", "LA"):
        background = Image.new(
            "RGB",
            image.size,
            (255, 255, 255)
        )

        alpha = image.getchannel("A")
        background.paste(image, mask=alpha)

        image = background

    elif image.mode == "P":
        image = image.convert("RGBA")

        background = Image.new(
            "RGB",
            image.size,
            (255, 255, 255)
        )

        alpha = image.getchannel("A")
        background.paste(image, mask=alpha)

        image = background

    elif image.mode != "RGB" and ext == "jpg":
        image = image.convert("RGB")

    return image, ext


def download_image(url):
    try:
        response = requests.get(
            url,
            headers=HEADERS,
            timeout=REQUEST_TIMEOUT,
            allow_redirects=True,
        )

        response.raise_for_status()

        if not response.content:
            return None, None, "Empty response"

        image = Image.open(BytesIO(response.content))
        image.load()

        image = ImageOps.exif_transpose(image)

        image, ext = prepare_image(
            image,
            image.format
        )

        buffer = BytesIO()

        if ext == "jpg":
            image.save(
                buffer,
                format="JPEG",
                quality=95,
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
                            f"Downloading {downloaded + skipped + 1} / {total_urls}"
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
                        use_container_width=True
                    )

            with open(zip_path, "rb") as f:
                st.download_button(
                    "Download ZIP",
                    data=f,
                    file_name="images.zip",
                    mime="application/zip",
                )
