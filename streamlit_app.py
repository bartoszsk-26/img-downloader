from PIL import Image
from io import BytesIO
import requests

def download_and_convert(url):
    response = requests.get(url, stream=True, timeout=25)
    response.raise_for_status()
    
    # Open image
    img = Image.open(BytesIO(response.content))
    
    # Convert TIFF to JPG
    if img.format.lower() == "tiff":
        img = img.convert("RGB")
        ext = "jpg"
    else:
        ext = img.format.lower() if img.format else "jpg"
        if ext in ["jpeg", "jpg"]:
            ext = "jpg"
        elif ext == "png":
            ext = "png"
        elif ext == "webp":
            ext = "webp"
        else:
            ext = "jpg"  # fallback

        # Ensure JPG RGB
        if ext == "jpg" and img.mode in ("RGBA", "LA"):
            img = img.convert("RGB")
    
    # Save to bytes
    buf = BytesIO()
    img.save(buf, format=ext.upper(), quality=95)
    buf.seek(0)
    
    return buf.read(), ext
