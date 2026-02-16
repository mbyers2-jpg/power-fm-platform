#!/usr/bin/env python3
"""
Content Parser for Social Media Agent.
Parses the Social-Media-Content-Package.md format into structured posts and calendar data.
"""

import re
import logging

log = logging.getLogger('social-media-agent')


def parse_content_package(filepath):
    """
    Parse a Social Media Content Package markdown file.
    Returns dict with keys: instagram, twitter, linkedin, facebook, calendar, hashtags, metadata
    """
    with open(filepath, 'r') as f:
        content = f.read()

    result = {
        'metadata': _parse_metadata(content),
        'instagram': [],
        'twitter': [],
        'linkedin': [],
        'facebook': [],
        'calendar': [],
        'hashtags': {},
    }

    # Split into major sections by top-level headers: # 1. INSTAGRAM, # 2. X / TWITTER, etc.
    sections = re.split(r'\n# (\d+)\.\s+', content)

    # sections[0] is the preamble, then alternating: section_number, section_content
    for i in range(1, len(sections), 2):
        section_num = sections[i]
        section_body = sections[i + 1] if i + 1 < len(sections) else ''

        # Determine which section based on content
        first_line = section_body.split('\n')[0].strip().upper()

        if 'INSTAGRAM' in first_line:
            result['instagram'] = _parse_platform_posts(section_body, 'instagram')
        elif 'TWITTER' in first_line or 'X /' in first_line:
            result['twitter'] = _parse_twitter_posts(section_body)
        elif 'LINKEDIN' in first_line:
            result['linkedin'] = _parse_platform_posts(section_body, 'linkedin')
        elif 'FACEBOOK' in first_line:
            result['facebook'] = _parse_platform_posts(section_body, 'facebook')
        elif 'CALENDAR' in first_line:
            result['calendar'] = _parse_calendar(section_body)
        elif 'HASHTAG' in first_line:
            result['hashtags'] = _parse_hashtags(section_body)

    return result


def _parse_metadata(content):
    """Extract title, author, date from the preamble."""
    meta = {}
    # Title from ## line
    title_match = re.search(r'## "([^"]+)"', content)
    if title_match:
        meta['title'] = title_match.group(1)

    # Author
    author_match = re.search(r'\*\*Author:\s*([^|*]+)', content)
    if author_match:
        meta['author'] = author_match.group(1).strip()

    # Date
    date_match = re.search(r'\*\*Prepared:\s*([^*]+)', content)
    if date_match:
        meta['date'] = date_match.group(1).strip()

    return meta


def _parse_platform_posts(section_body, platform):
    """Parse posts from an Instagram, LinkedIn, or Facebook section."""
    posts = []
    # Split on ### headers
    post_blocks = re.split(r'### ', section_body)

    for block in post_blocks[1:]:  # Skip pre-header content
        post = _parse_single_post(block, platform)
        if post:
            posts.append(post)

    return posts


def _parse_single_post(block, platform):
    """Parse a single post block into structured data."""
    lines = block.strip().split('\n')
    if not lines:
        return None

    # First line is the title
    title = lines[0].strip().rstrip('*').strip()

    # Extract the body (between **Caption:** and the next ** section or ---)
    body = ''
    hashtags = ''
    visual = ''
    linkedin_title = ''

    full_text = '\n'.join(lines[1:])

    # Check for LinkedIn title format: **Title: ...**
    lt_match = re.search(r'\*\*Title:\s*([^*]+)\*\*', full_text)
    if lt_match:
        linkedin_title = lt_match.group(1).strip()

    # Extract caption/body
    caption_match = re.search(
        r'\*\*Caption:\*\*\s*\n(.*?)(?=\n\*\*Suggested Visual|$)',
        full_text, re.DOTALL
    )
    if caption_match:
        body_text = caption_match.group(1).strip()
    else:
        # For posts without explicit **Caption:** marker (LinkedIn, Facebook)
        # Take everything between title line and **Suggested Visual**
        visual_idx = full_text.find('**Suggested Visual')
        if visual_idx > 0:
            body_text = full_text[:visual_idx].strip()
        else:
            body_text = full_text.strip()

        # Remove the **Title:** line if present
        if lt_match:
            body_text = body_text.replace(lt_match.group(0), '').strip()

    # Extract hashtags from body
    hashtag_matches = re.findall(r'#\w+', body_text)
    if hashtag_matches:
        hashtags = ' '.join(hashtag_matches)
        # Remove the hashtag line from body
        body_lines = []
        for line in body_text.split('\n'):
            if line.strip() and all(word.startswith('#') for word in line.strip().split()):
                continue  # Skip pure hashtag lines
            body_lines.append(line)
        body = '\n'.join(body_lines).strip()
    else:
        body = body_text.strip()

    # Clean up body - remove leading/trailing dashes and whitespace
    body = re.sub(r'^---\s*', '', body).strip()
    body = re.sub(r'\s*---$', '', body).strip()

    # Extract visual description
    visual_match = re.search(r'\*\*Suggested Visual:\*\*\s*(.*?)(?=\n---|\Z)', full_text, re.DOTALL)
    if visual_match:
        visual = visual_match.group(1).strip()

    if not body:
        return None

    post_data = {
        'title': title,
        'body': body,
        'hashtags': hashtags,
        'media_description': visual,
        'platform': platform,
        'content_type': 'post',
    }

    if linkedin_title:
        post_data['linkedin_title'] = linkedin_title

    return post_data


def _parse_twitter_posts(section_body):
    """Parse Twitter section including regular tweets and threads."""
    posts = []
    post_blocks = re.split(r'### ', section_body)

    for block in post_blocks[1:]:
        lines = block.strip().split('\n')
        if not lines:
            continue

        title = lines[0].strip().rstrip('*').strip()

        # Check if this is a thread
        if 'thread' in title.lower() or 'Thread' in title:
            thread_posts = _parse_twitter_thread(block, title)
            posts.extend(thread_posts)
        else:
            # Single tweet
            body = '\n'.join(lines[1:]).strip()
            # Remove leading/trailing ---
            body = re.sub(r'^---\s*', '', body).strip()
            body = re.sub(r'\s*---$', '', body).strip()

            # Extract hashtags
            hashtag_matches = re.findall(r'#\w+', body)
            hashtags = ' '.join(hashtag_matches) if hashtag_matches else ''

            if body:
                posts.append({
                    'title': title,
                    'body': body,
                    'hashtags': hashtags,
                    'media_description': '',
                    'platform': 'twitter',
                    'content_type': 'tweet',
                })

    return posts


def _parse_twitter_thread(block, title):
    """Parse a Twitter thread into individual tweet posts."""
    posts = []

    # Find individual tweets by pattern: **Tweet N/M:**
    tweet_blocks = re.split(r'\*\*Tweet (\d+)/(\d+):\*\*', block)

    # tweet_blocks: [preamble, num, total, body, num, total, body, ...]
    thread_title = title
    total_tweets = 0

    for i in range(1, len(tweet_blocks), 3):
        tweet_num = int(tweet_blocks[i])
        total_tweets = int(tweet_blocks[i + 1])
        tweet_body = tweet_blocks[i + 2].strip() if i + 2 < len(tweet_blocks) else ''

        # Clean up
        tweet_body = re.sub(r'^---\s*', '', tweet_body).strip()
        tweet_body = re.sub(r'\s*---$', '', tweet_body).strip()

        hashtag_matches = re.findall(r'#\w+', tweet_body)
        hashtags = ' '.join(hashtag_matches) if hashtag_matches else ''

        posts.append({
            'title': f"{thread_title} ({tweet_num}/{total_tweets})",
            'body': tweet_body,
            'hashtags': hashtags,
            'media_description': '',
            'platform': 'twitter',
            'content_type': 'thread',
            'thread_position': tweet_num,
            'thread_total': total_tweets,
        })

    return posts


def _parse_calendar(section_body):
    """Parse the content calendar tables into structured data."""
    entries = []

    # Find all table rows (skip header and separator rows)
    lines = section_body.split('\n')

    current_phase = ''
    for line in lines:
        # Detect phase headers
        if line.startswith('## '):
            current_phase = line.strip('# ').strip()
            continue

        # Parse table rows: | Day N (Day) | Platform | Content | Post Type |
        row_match = re.match(
            r'\|\s*Day\s+(\d+)\s*\((\w+)\)\s*\|\s*([^|]+)\|\s*([^|]+)\|\s*([^|]+)\|',
            line
        )
        if row_match:
            day_num = int(row_match.group(1))
            day_name = row_match.group(2).strip()
            platform = row_match.group(3).strip().lower()
            content_ref = row_match.group(4).strip()
            post_type = row_match.group(5).strip()

            # Normalize platform names
            if 'twitter' in platform or 'x/' in platform:
                platform = 'twitter'
            elif 'instagram' in platform:
                platform = 'instagram'
            elif 'linkedin' in platform:
                platform = 'linkedin'
            elif 'facebook' in platform:
                platform = 'facebook'

            entries.append({
                'day': day_num,
                'day_name': day_name,
                'platform': platform,
                'content_ref': content_ref,
                'post_type': post_type,
                'phase': current_phase,
            })

    # Also handle Day 14 "All Platforms" entries
    for line in lines:
        all_match = re.match(
            r'\|\s*Day\s+(\d+)\s*\((\w+)\)\s*\|\s*All Platforms\s*\|\s*([^|]+)\|\s*([^|]+)\|',
            line
        )
        if all_match:
            day_num = int(all_match.group(1))
            day_name = all_match.group(2).strip()
            content_ref = all_match.group(3).strip()
            post_type = all_match.group(4).strip()

            for platform in ['instagram', 'twitter', 'linkedin', 'facebook']:
                # Check we haven't already added this
                existing = [e for e in entries if e['day'] == day_num and e['platform'] == platform]
                if not existing:
                    entries.append({
                        'day': day_num,
                        'day_name': day_name,
                        'platform': platform,
                        'content_ref': content_ref,
                        'post_type': post_type,
                        'phase': current_phase,
                    })

    return entries


def _parse_hashtags(section_body):
    """Parse the hashtag strategy section."""
    result = {
        'primary': [],
        'secondary': {},
        'platform_notes': {},
    }

    lines = section_body.split('\n')
    current_section = ''
    current_topic = ''

    for line in lines:
        stripped = line.strip()

        if '## Primary' in stripped:
            current_section = 'primary'
            continue
        elif '## Secondary' in stripped:
            current_section = 'secondary'
            continue
        elif '## Platform-Specific' in stripped:
            current_section = 'platform'
            continue

        if current_section == 'primary':
            tag = re.match(r'-\s*(#\w+)', stripped)
            if tag:
                result['primary'].append(tag.group(1))

        elif current_section == 'secondary':
            topic = re.match(r'###\s*Topic:\s*(.+)', stripped)
            if topic:
                current_topic = topic.group(1).strip()
                result['secondary'][current_topic] = []
                continue
            tag = re.match(r'-\s*(#\w+)', stripped)
            if tag and current_topic:
                result['secondary'][current_topic].append(tag.group(1))

        elif current_section == 'platform':
            platform_header = re.match(r'###\s*(\w+)', stripped)
            if platform_header:
                current_topic = platform_header.group(1).lower()
                if 'twitter' in current_topic or current_topic == 'x':
                    current_topic = 'twitter'
                result['platform_notes'][current_topic] = []
                continue
            if stripped.startswith('-') and current_topic:
                result['platform_notes'][current_topic].append(stripped.lstrip('- '))

    return result


def match_calendar_to_posts(parsed_data):
    """
    Match calendar entries to parsed posts.
    Returns list of dicts: {calendar_entry, post, platform}
    """
    matches = []

    # Build lookup of posts by platform and partial title matching
    post_lookup = {}
    for platform in ['instagram', 'twitter', 'linkedin', 'facebook']:
        post_lookup[platform] = parsed_data.get(platform, [])

    for cal in parsed_data.get('calendar', []):
        platform = cal['platform']
        content_ref = cal['content_ref']
        available_posts = post_lookup.get(platform, [])

        matched_post = None

        # Try to match by content reference keywords
        ref_lower = content_ref.lower()

        for post in available_posts:
            title_lower = post['title'].lower()

            # Direct title substring match
            if any(keyword in title_lower for keyword in _extract_keywords(ref_lower)):
                matched_post = post
                break

        matches.append({
            'calendar': cal,
            'post': matched_post,
            'platform': platform,
        })

    return matches


def _extract_keywords(ref_text):
    """Extract matching keywords from a calendar content reference."""
    keywords = []
    # Common patterns in content references
    patterns = [
        r'(ig post \d+)', r'(tweet \d+)', r'(linkedin post \d+)', r'(fb post \d+)',
        r'(thread)', r'(book announcement)', r'(report)', r'(stat)',
        r'(chapter)', r'(why i wrote)', r'(behind)', r'(credibility)',
        r'(free report)', r'(data.driven)', r'(insight)', r'(promo)',
        r'(community)', r'(super bowl)', r'(streaming)', r'(operator)',
        r'(recap)', r'(coming soon)',
    ]
    for p in patterns:
        match = re.search(p, ref_text)
        if match:
            keywords.append(match.group(1))

    # Also add individual significant words
    for word in ref_text.split():
        if len(word) > 4 and word not in ('with', 'from', 'that', 'this', 'post', 'type'):
            keywords.append(word)

    return keywords


def load_content_to_db(conn, filepath, campaign_name=None):
    """
    Parse a content package and load all posts into the database.
    Returns (campaign_id, post_count).
    """
    from database import create_campaign, create_post

    parsed = parse_content_package(filepath)
    meta = parsed.get('metadata', {})

    if not campaign_name:
        campaign_name = meta.get('title', 'Unnamed Campaign')

    campaign_id = create_campaign(
        conn, campaign_name,
        description=f"Loaded from {filepath}",
        content_source=filepath,
    )

    post_count = 0

    # Track thread groupings for Twitter
    thread_group_id = None

    for platform in ['instagram', 'twitter', 'linkedin', 'facebook']:
        posts = parsed.get(platform, [])
        for post_data in posts:
            # Determine calendar day by matching
            calendar_day = _find_calendar_day(parsed.get('calendar', []), platform, post_data)

            kwargs = {
                'content_type': post_data.get('content_type', 'post'),
                'title': post_data.get('title', ''),
                'hashtags': post_data.get('hashtags', ''),
                'media_description': post_data.get('media_description', ''),
                'calendar_day': calendar_day,
            }

            # Handle thread grouping
            if post_data.get('content_type') == 'thread':
                if post_data.get('thread_position') == 1:
                    # First tweet in thread - create and set group id after
                    pid = create_post(conn, campaign_id, platform, post_data['body'], **kwargs)
                    thread_group_id = pid
                    update_post_thread = conn.execute(
                        "UPDATE posts SET thread_id = ?, thread_position = ? WHERE id = ?",
                        (pid, 1, pid)
                    )
                    conn.commit()
                else:
                    kwargs['thread_id'] = thread_group_id
                    kwargs['thread_position'] = post_data.get('thread_position', 1)
                    pid = create_post(conn, campaign_id, platform, post_data['body'], **kwargs)
            else:
                thread_group_id = None
                pid = create_post(conn, campaign_id, platform, post_data['body'], **kwargs)

            post_count += 1
            log.info(f"Loaded post {pid}: [{platform}] {post_data.get('title', '')[:50]}")

    log.info(f"Loaded {post_count} posts into campaign {campaign_id} ({campaign_name})")
    return campaign_id, post_count


def _find_calendar_day(calendar_entries, platform, post_data):
    """Try to match a post to a calendar day."""
    title = post_data.get('title', '').lower()

    for cal in calendar_entries:
        if cal['platform'] != platform:
            continue

        ref = cal['content_ref'].lower()

        # Try specific matches
        # "IG Post 1" -> matches title containing "ig post 1"
        # "Tweet 3" -> matches title containing "tweet 3"
        # "LinkedIn Post 1" -> matches "linkedin post 1"
        # "FB Post 1" -> matches "fb post 1"
        # "Thread: The $800" -> matches "thread"

        # Extract post number from reference
        num_match = re.search(r'(?:ig post|tweet|linkedin post|fb post)\s*(\d+)', ref)
        if num_match:
            ref_num = num_match.group(1)
            ref_type = ref[:num_match.start()].strip()

            # Check title for matching number
            title_num_match = re.search(r'(\d+)', title)
            if title_num_match and title_num_match.group(1) == ref_num:
                # Verify platform prefix matches
                if platform == 'instagram' and 'ig' in ref:
                    return cal['day']
                elif platform == 'twitter' and 'tweet' in ref:
                    return cal['day']
                elif platform == 'linkedin' and 'linkedin' in ref:
                    return cal['day']
                elif platform == 'facebook' and 'fb' in ref:
                    return cal['day']

        # Thread match
        if 'thread' in ref and post_data.get('content_type') == 'thread':
            if post_data.get('thread_position', 1) == 1:
                return cal['day']

    return None


def update_post(conn, post_id, **kwargs):
    """Import from database module to avoid circular imports."""
    from database import update_post as db_update_post
    db_update_post(conn, post_id, **kwargs)
