"""Bounded inline image attachments shared by chat and model adapters."""
import base64
import binascii

MAX_IMAGES = 4
MAX_IMAGE_BYTES = 5 * 1024 * 1024
MAX_REQUEST_BYTES = 29 * 1024 * 1024
TYPES = ('image/png', 'image/jpeg', 'image/webp', 'image/gif')


def validate_images(images):
    if not isinstance(images, list) or len(images) > MAX_IMAGES:
        raise ValueError('Attach up to 4 images (PNG, JPEG, WebP or GIF), at most 5 MB each.')
    clean = []
    for image in images:
        if not isinstance(image, dict):
            raise ValueError('Invalid image attachment.')
        name, url = image.get('name'), image.get('data_url')
        if not isinstance(name, str) or not 1 <= len(name) <= 255 or not isinstance(url, str):
            raise ValueError('Invalid image attachment.')
        header, sep, data = url.partition(',')
        mime = header.removeprefix('data:').removesuffix(';base64')
        if not sep or mime not in TYPES or header != f'data:{mime};base64' or len(data) > 4 * ((MAX_IMAGE_BYTES + 2) // 3):
            raise ValueError('Images must be PNG, JPEG, WebP or GIF and at most 5 MB each.')
        try:
            raw = base64.b64decode(data, validate=True)
        except (ValueError, binascii.Error):
            raise ValueError('Invalid image encoding.') from None
        valid = {'image/png': raw.startswith(b'\x89PNG\r\n\x1a\n'),
                 'image/jpeg': raw.startswith(b'\xff\xd8\xff'),
                 'image/gif': raw.startswith((b'GIF87a', b'GIF89a')),
                 'image/webp': raw.startswith(b'RIFF') and raw[8:12] == b'WEBP'}[mime]
        if not valid or len(raw) > MAX_IMAGE_BYTES:
            raise ValueError('Image format or size is invalid.')
        clean.append({'name': name, 'data_url': url})
    return clean


def model_input(text, images):
    if not images:
        return text
    content = [{'type': 'input_text', 'text': text}] if text else []
    content.extend({'type': 'input_image', 'image_url': image['data_url']} for image in images)
    return [{'role': 'user', 'content': content}]


def anthropic_content(content):
    blocks = []
    for block in content:
        if block['type'] == 'input_text':
            blocks.append({'type': 'text', 'text': block['text']})
        elif block['type'] == 'input_image':
            header, data = block['image_url'].split(',', 1)
            blocks.append({'type': 'image', 'source': {'type': 'base64',
                'media_type': header[5:-7], 'data': data}})
        else:
            raise ValueError('Unsupported image message content.')
    return blocks
