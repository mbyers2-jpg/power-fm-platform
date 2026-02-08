"""
File classifier — determines where a file should go based on its name,
extension, and content hints.
"""

import os
import re

HOME = os.path.expanduser('~')

# Extension → category mapping
EXTENSION_MAP = {
    # Audio
    '.mp3': 'audio', '.wav': 'audio', '.m4a': 'audio', '.flac': 'audio',
    '.aac': 'audio', '.ogg': 'audio', '.amr': 'audio', '.aiff': 'audio',
    # DAW projects
    '.band': 'daw_project',
    # MIDI
    '.mid': 'midi', '.midi': 'midi',
    # Video
    '.mp4': 'video', '.mov': 'video', '.avi': 'video', '.mkv': 'video',
    '.m4v': 'video', '.wmv': 'video',
    # Images
    '.jpg': 'image', '.jpeg': 'image', '.png': 'image', '.gif': 'image',
    '.heic': 'image', '.webp': 'image', '.tiff': 'image', '.bmp': 'image',
    '.svg': 'image',
    # Documents
    '.pdf': 'document', '.docx': 'document', '.doc': 'document',
    '.pages': 'document', '.txt': 'document', '.rtf': 'document',
    '.html': 'document',
    # Presentations
    '.pptx': 'presentation', '.key': 'presentation',
    # Spreadsheets
    '.xlsx': 'spreadsheet', '.xlsm': 'spreadsheet', '.xls': 'spreadsheet',
    '.csv': 'spreadsheet',
    # Installers
    '.dmg': 'installer', '.pkg': 'installer', '.app': 'installer',
    '.zip': 'archive', '.gz': 'archive', '.tar': 'archive', '.rar': 'archive',
}

# Known artist names for auto-routing
KNOWN_ARTISTS = [
    'firefly', 'glenn', 'lord afrixana', 'afrixana', 'stephen fulton',
    'fulton', 'waddell', 'sophie', 'bewill', 'pheelz', 'young athena',
    'ribbon', 'phonebook',
]

# Known project names
KNOWN_PROJECTS = {
    'breakr': 'Breakr-Visa', 'visa': 'Breakr-Visa',
    'cero': 'CERO', 'cworks': 'CWORKS',
    'protect the culture': 'Protect-The-Culture', 'ptc': 'Protect-The-Culture',
    'cool boy': 'Cool-Boy-Shoe-Surgeon', 'shoe surgeon': 'Cool-Boy-Shoe-Surgeon',
    'meridian': 'Meridian-InnerVision', 'innervision': 'Meridian-InnerVision',
    'motown': 'Motown-Bodega', 'bodega': 'Motown-Bodega',
    'revolt': 'Revolt',
}

# Audio sub-classification keywords
MASTER_KEYWORDS = ['master', 'final', 'distribution']
STEM_KEYWORDS = ['stem', 'vocal', 'instrumental', 'backing', 'acapella']
DEMO_KEYWORDS = ['demo', 'idea', 'rough', 'draft', 'wip', 'ruff']
MIX_KEYWORDS = ['mix', 'remix', 'dromix', 'cleaned up']

# Document sub-classification keywords
CONTRACT_KEYWORDS = [
    'agreement', 'contract', 'msa', 'sow', 'addendum', 'amendment',
    'execution', 'signed', 'docusign', 'signature',
]
NDA_KEYWORDS = ['nda', 'non-disclosure', 'mutual nda', 'confidential']
PROPOSAL_KEYWORDS = [
    'proposal', 'pitch', 'deck', 'overview', 'brief', 'strategy',
    'one-sheet', 'one sheet', 'teaser', 'epk',
]
INVOICE_KEYWORDS = ['invoice', 'payment', 'receipt', 'billing', 'vendor']
FINANCIAL_KEYWORDS = [
    'financial', 'budget', 'forecast', 'model', 'investor', 'fund',
    'capital', 'equity', 'valuation',
]
PERSONAL_KEYWORDS = [
    'passport', 'license', 'dmv', 'social security', 'birth certificate',
    'medical', 'colonoscopy', 'prescription', 'health',
    'incorporation', 'ubo', 'tax', 'w-2', '1099',
]

# Screenshot detection
SCREENSHOT_PATTERN = re.compile(r'screenshot|screen.?shot|screen.?cap', re.IGNORECASE)


def classify_file(filename):
    """
    Classify a file and return its destination path.
    Returns (destination_dir, reason) tuple.
    """
    name_lower = filename.lower()
    base_name = os.path.splitext(filename)[0].lower()
    ext = os.path.splitext(filename)[1].lower()

    category = EXTENSION_MAP.get(ext, 'unknown')

    # --- Installers: always quarantine ---
    if category == 'installer':
        return os.path.join(HOME, 'Downloads'), 'installer_kept_in_downloads'

    # --- Archives: keep in Downloads for manual sort ---
    if category == 'archive':
        return os.path.join(HOME, 'Downloads'), 'archive_kept_in_downloads'

    # --- Screenshots ---
    if SCREENSHOT_PATTERN.search(filename):
        return os.path.join(HOME, 'Pictures', 'Screenshots'), 'screenshot'

    # --- Check for known project match ---
    for keyword, project_folder in KNOWN_PROJECTS.items():
        if keyword in base_name:
            return os.path.join(HOME, 'Documents', 'Projects', project_folder), f'project_match:{keyword}'

    # --- Check for known artist match ---
    for artist in KNOWN_ARTISTS:
        if artist in base_name:
            artist_folder = artist.title().replace(' ', '-')
            # Artist audio → Music, artist docs → Documents/Artists
            if category in ('audio', 'daw_project', 'midi'):
                if any(kw in base_name for kw in MASTER_KEYWORDS):
                    return os.path.join(HOME, 'Music', 'Masters'), f'artist_master:{artist}'
                if any(kw in base_name for kw in STEM_KEYWORDS):
                    return os.path.join(HOME, 'Music', 'Stems'), f'artist_stem:{artist}'
                if any(kw in base_name for kw in MIX_KEYWORDS):
                    return os.path.join(HOME, 'Music', 'Rough-Mixes'), f'artist_mix:{artist}'
                return os.path.join(HOME, 'Music', 'Demos'), f'artist_demo:{artist}'
            else:
                return os.path.join(HOME, 'Documents', 'Artists', artist_folder), f'artist_doc:{artist}'

    # --- Audio files ---
    if category == 'audio':
        if any(kw in base_name for kw in MASTER_KEYWORDS):
            return os.path.join(HOME, 'Music', 'Masters'), 'audio_master'
        if any(kw in base_name for kw in STEM_KEYWORDS):
            return os.path.join(HOME, 'Music', 'Stems'), 'audio_stem'
        if any(kw in base_name for kw in MIX_KEYWORDS):
            return os.path.join(HOME, 'Music', 'Rough-Mixes'), 'audio_mix'
        return os.path.join(HOME, 'Music', 'Demos'), 'audio_default'

    if category == 'daw_project':
        return os.path.join(HOME, 'Music', 'Projects'), 'daw_project'

    if category == 'midi':
        return os.path.join(HOME, 'Music', 'MIDI'), 'midi'

    # --- Video files ---
    if category == 'video':
        return os.path.join(HOME, 'Movies'), 'video'

    # --- Images ---
    if category == 'image':
        return os.path.join(HOME, 'Pictures', 'Reference-Images'), 'image_default'

    # --- Personal documents ---
    if any(kw in base_name for kw in PERSONAL_KEYWORDS):
        if any(kw in base_name for kw in ['passport', 'license', 'dmv', 'id', 'birth']):
            return os.path.join(HOME, 'Documents', 'Personal', 'ID-Documents'), 'personal_id'
        if any(kw in base_name for kw in ['medical', 'colonoscopy', 'prescription', 'health']):
            return os.path.join(HOME, 'Documents', 'Personal', 'Medical'), 'personal_medical'
        if any(kw in base_name for kw in ['incorporation', 'ubo', 'tax', 'w-2', '1099']):
            return os.path.join(HOME, 'Documents', 'Personal', 'Legal'), 'personal_legal'
        return os.path.join(HOME, 'Documents', 'Personal'), 'personal_general'

    # --- Presentations ---
    if category == 'presentation':
        return os.path.join(HOME, 'Documents', 'Business', 'Pitch-Decks'), 'presentation'

    # --- Spreadsheets ---
    if category == 'spreadsheet':
        if any(kw in base_name for kw in FINANCIAL_KEYWORDS):
            return os.path.join(HOME, 'Documents', 'Business', 'Financial'), 'spreadsheet_financial'
        return os.path.join(HOME, 'Documents', 'Business', 'Financial'), 'spreadsheet_default'

    # --- Documents (PDF, DOCX, etc.) ---
    if category == 'document':
        if any(kw in base_name for kw in NDA_KEYWORDS):
            return os.path.join(HOME, 'Documents', 'Business', 'NDAs'), 'nda'
        if any(kw in base_name for kw in CONTRACT_KEYWORDS):
            return os.path.join(HOME, 'Documents', 'Business', 'Contracts-Agreements'), 'contract'
        if any(kw in base_name for kw in INVOICE_KEYWORDS):
            return os.path.join(HOME, 'Documents', 'Business', 'Invoices'), 'invoice'
        if any(kw in base_name for kw in FINANCIAL_KEYWORDS):
            return os.path.join(HOME, 'Documents', 'Business', 'Financial'), 'financial'
        if any(kw in base_name for kw in PROPOSAL_KEYWORDS):
            return os.path.join(HOME, 'Documents', 'Business', 'Proposals'), 'proposal'
        return os.path.join(HOME, 'Documents', 'Business', 'Proposals'), 'document_default'

    # --- Unknown: leave in place ---
    return None, 'unknown_type'
