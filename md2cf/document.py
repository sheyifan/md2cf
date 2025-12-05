import hashlib
import os
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

import chardet
import mistune
import yaml
from yaml.parser import ParserError

from md2cf.confluence_renderer import ConfluenceRenderer, RelativeLink
from md2cf.ignored_files import GitRepository


class Page(object):
    def __init__(
        self,
        title: Optional[str],
        body: str,
        content_type: Optional[str] = "page",
        attachments: Optional[List[Path]] = None,
        file_path: Optional[Path] = None,
        page_id: str = None,
        parent_id: str = None,
        parent_title: str = None,
        space: str = None,
        labels: Optional[List[str]] = None,
        relative_links: Optional[List[RelativeLink]] = None,
    ):
        self.title = title
        self.original_title = None
        self.body = body
        self.content_type = content_type
        self.file_path = file_path
        self.attachments = attachments
        if self.attachments is None:
            self.attachments: List[Path] = list()
        self.relative_links = relative_links
        if self.relative_links is None:
            self.relative_links: List[RelativeLink] = list()
        self.page_id = page_id
        self.parent_id = parent_id
        self.parent_title = parent_title
        self.space = space
        self.labels = labels

    def get_content_hash(self):
        return hashlib.sha1(self.body.encode()).hexdigest()

    def __repr__(self):
        return "Page({})".format(
            ", ".join(
                [
                    "{}={}".format(name, repr(value))
                    for name, value in [
                        ["title", self.title],
                        ["file_path", self.file_path],
                        ["page_id", self.page_id],
                        ["parent_id", self.parent_id],
                        ["parent_title", self.parent_title],
                        ["space", self.space],
                        [
                            "body",
                            f"{self.body[:40]} [...]"
                            if len(self.body) > 40
                            else self.body,
                        ],
                    ]
                ]
            )
        )


def find_non_empty_parent_path(
    current_dir: Path, folder_data: Dict[Path, Dict[str, Any]], default: Path
) -> Path:
    for parent in current_dir.parents:
        if parent in folder_data and folder_data[parent]["n_files"]:
            return parent
    return default.absolute()


def get_pages_from_directory(
    file_path: Path,
    collapse_single_pages: bool = False,
    skip_empty: bool = False,
    collapse_empty: bool = False,
    beautify_folders: bool = False,
    use_pages_file: bool = False,
    strip_header: bool = False,
    remove_text_newlines: bool = False,
    use_gitignore: bool = True,
    enable_relative_links: bool = False,
) -> List[Page]:
    """
    Collect a list of markdown files recursively under the file_path directory.

    :param file_path: The starting path from which to search
    :param collapse_single_pages:
    :param skip_empty:
    :param collapse_empty:
    :param beautify_folders:
    :param use_pages_file:
    :param strip_header:
    :param remove_text_newlines:
    :param use_gitignore: Use .gitignore files to skip unwanted markdown in directory
      search
    :param enable_relative_links: extract all relative links and replace them with
      placeholders
    :return: A list of paths to the markdown files to upload.
    """
    processed_pages = list()
    base_path = file_path.resolve()
    folder_data = dict()
    git_repo = GitRepository(file_path, use_gitignore=use_gitignore)
    
    # Track which markdown files are used as folder content (to skip them later)
    files_used_as_folder_content = set()
    
    # First pass: identify all markdown files that will be used as folder content
    for current_path, directories, file_names in os.walk(file_path):
        current_path = Path(current_path).resolve()
        if git_repo.is_ignored(current_path):
            continue
        
        # For each subdirectory, check if there's a matching markdown file
        for subdir in directories:
            subdir_path = current_path / subdir
            if git_repo.is_ignored(subdir_path):
                continue
            
            # Look for a markdown file with the same stem as the subdirectory
            potential_file = current_path / f"{subdir}.md"
            if potential_file.exists() and not git_repo.is_ignored(potential_file):
                files_used_as_folder_content.add(potential_file.resolve())

    # Second pass: process all files and folders
    for current_path, directories, file_names in os.walk(file_path):
        current_path = Path(current_path).resolve()

        if git_repo.is_ignored(current_path):
            continue

        markdown_files = [
            Path(current_path, file_name)
            for file_name in file_names
            if file_name.endswith(".md")
        ]
        # Filter out ignored files
        markdown_files = [
            path for path in markdown_files if not git_repo.is_ignored(path)
        ]

        # Build a set of subdirectory names in the current path
        subdirs_in_current = {d for d in directories}

        # Check if there's a markdown file at the parent level with matching stem
        folder_content_file = None
        if current_path != base_path:
            # Check if a markdown file with the same stem exists in the parent directory
            potential_file = current_path.parent / f"{current_path.name}.md"
            if potential_file.resolve() in files_used_as_folder_content:
                folder_content_file = potential_file

        folder_data[current_path] = {
            "n_files": len(markdown_files),
            "content_file": folder_content_file,
            "subdirs": subdirs_in_current
        }

        # we'll capture title and path of the parent folder for this folder:
        folder_parent_title = None
        folder_parent_path = None

        # title for this folder's page (as parent of its children):
        parent_page_title = None
        # title for the folder (same as above except when collapsing):
        folder_title = None

        if current_path != base_path:
            # TODO: add support for .pages file to read folder title
            if skip_empty or collapse_empty:
                folder_parent_path = find_non_empty_parent_path(
                    current_path, folder_data, default=file_path
                )
            else:
                folder_parent_path = current_path.parent

            folder_parent_title = folder_data[folder_parent_path]["title"]
            parent_page_title = current_path.name
            if len(markdown_files) == 1 and collapse_single_pages:
                parent_page_title = folder_parent_title
                folder_title = None
            else:
                if collapse_empty:
                    parent_page_title = str(
                        current_path.relative_to(folder_parent_path)
                    )
                if beautify_folders:
                    parent_page_title = (
                        current_path.name.replace("-", " ")
                        .replace("_", " ")
                        .capitalize()
                    )
                folder_title = parent_page_title
        if use_pages_file and ".pages" in file_names:
            with open(current_path.joinpath(".pages")) as pages_fp:
                pages_file_contents = yaml.safe_load(pages_fp)
            if "title" in pages_file_contents:
                parent_page_title = pages_file_contents["title"]
                folder_title = parent_page_title

        folder_data[current_path]["title"] = folder_title

        # Prepare folder page with content if a matching markdown file exists
        folder_page_body = ""
        folder_page_file_path = None
        folder_page_attachments = []
        folder_page_relative_links = []
        
        if folder_data[current_path]["content_file"]:
            # Use the content from the matching markdown file
            content_page = get_page_data_from_file_path(
                folder_data[current_path]["content_file"],
                strip_header=strip_header,
                remove_text_newlines=remove_text_newlines,
                enable_relative_links=enable_relative_links,
            )
            folder_page_body = content_page.body
            folder_page_file_path = content_page.file_path
            folder_page_attachments = content_page.attachments
            folder_page_relative_links = content_page.relative_links
            # Override folder title with the document title if available
            if content_page.title:
                folder_title = content_page.title
                parent_page_title = content_page.title  # Update parent_page_title for children
                folder_data[current_path]["title"] = folder_title

        if folder_title is not None and (
            markdown_files or (directories and not skip_empty and not collapse_empty)
        ):
            processed_pages.append(
                Page(
                    title=folder_title,
                    parent_title=folder_parent_title,
                    body=folder_page_body,
                    file_path=folder_page_file_path,
                    attachments=folder_page_attachments,
                    relative_links=folder_page_relative_links,
                )
            )

        for markdown_file in markdown_files:
            # Skip this file if it was already used as the folder's content
            if folder_data[current_path]["content_file"] == markdown_file:
                continue
            
            # Skip this file if it's being used as content for any folder
            if markdown_file.resolve() in files_used_as_folder_content:
                continue
                
            processed_page = get_page_data_from_file_path(
                markdown_file,
                strip_header=strip_header,
                remove_text_newlines=remove_text_newlines,
                enable_relative_links=enable_relative_links,
            )
            processed_page.parent_title = parent_page_title
            processed_pages.append(processed_page)

            # This replaces the title for the current folder with the title for the
            # document we just parsed, so things below this folder will be correctly
            # parented to the collapsed document.
            if len(markdown_files) == 1 and collapse_single_pages:
                folder_data[current_path]["title"] = processed_page.title

    return processed_pages


def get_page_data_from_file_path(
    file_path: Path,
    strip_header: bool = False,
    remove_text_newlines: bool = False,
    enable_relative_links: bool = False,
) -> Page:
    if not isinstance(file_path, Path):
        file_path = Path(file_path)

    try:
        with open(file_path, encoding='utf-8') as file_handle:
            markdown_lines = file_handle.readlines()
    except UnicodeDecodeError:
        with open(file_path, "rb") as file_handle:
            detected_encoding = chardet.detect(file_handle.read())
        with open(file_path, encoding=detected_encoding["encoding"]) as file_handle:
            markdown_lines = file_handle.readlines()

    page = get_page_data_from_lines(
        markdown_lines,
        strip_header=strip_header,
        remove_text_newlines=remove_text_newlines,
        enable_relative_links=enable_relative_links,
    )

    if not page.title:
        page.title = file_path.stem

    page.file_path = file_path

    return page


def get_page_data_from_lines(
    markdown_lines: List[str],
    strip_header: bool = False,
    remove_text_newlines: bool = False,
    enable_relative_links: bool = False,
) -> Page:
    frontmatter = get_document_frontmatter(markdown_lines)
    if "frontmatter_end_line" in frontmatter:
        markdown_lines = markdown_lines[frontmatter["frontmatter_end_line"] :]

    page = parse_page(
        markdown_lines,
        strip_header=strip_header,
        remove_text_newlines=remove_text_newlines,
        enable_relative_links=enable_relative_links,
    )

    if "title" in frontmatter:
        page.title = frontmatter["title"]

    if "labels" in frontmatter:
        if isinstance(frontmatter["labels"], list):
            page.labels = [str(label) for label in frontmatter["labels"]]
        else:
            raise TypeError(
                "the labels section in the frontmatter " "must be a list of strings"
            )
    return page


def parse_page(
    markdown_lines: List[str],
    strip_header: bool = False,
    remove_text_newlines: bool = False,
    enable_relative_links: bool = False,
) -> Page:
    renderer = ConfluenceRenderer(
        use_xhtml=True,
        strip_header=strip_header,
        remove_text_newlines=remove_text_newlines,
        enable_relative_links=enable_relative_links,
    )
    confluence_mistune = mistune.Markdown(renderer=renderer)
    confluence_content = confluence_mistune("".join(markdown_lines))

    page = Page(
        title=renderer.title,
        body=confluence_content,
        attachments=renderer.attachments,
        relative_links=renderer.relative_links,
    )

    return page


def get_document_frontmatter(markdown_lines: List[str]) -> Dict[str, Any]:
    frontmatter_yaml = ""
    frontmatter_end_line = 0
    if markdown_lines and markdown_lines[0] == "---\n":
        for index, line in enumerate(markdown_lines[1:]):
            if line == "---\n":
                frontmatter_end_line = index + 2
                break
            else:
                frontmatter_yaml += line
    frontmatter = None
    if frontmatter_yaml and frontmatter_end_line:
        try:
            frontmatter = yaml.safe_load(frontmatter_yaml)
        except ParserError:
            pass
    if isinstance(frontmatter, dict):
        frontmatter["frontmatter_end_line"] = frontmatter_end_line
    else:
        frontmatter = {}

    return frontmatter


class SummaryItem:
    """Represents an item in the SUMMARY.md file"""
    
    def __init__(
        self,
        title: str,
        path: Optional[Path] = None,
        is_part_title: bool = False,
        is_separator: bool = False,
        children: Optional[List["SummaryItem"]] = None,
    ):
        self.title = title
        self.path = path
        self.is_part_title = is_part_title
        self.is_separator = is_separator
        self.children = children if children is not None else []
    
    def __repr__(self):
        return (
            f"SummaryItem(title={self.title!r}, path={self.path}, "
            f"is_part_title={self.is_part_title}, children={len(self.children)})"
        )


def parse_summary_md(summary_path: Path) -> List[SummaryItem]:
    """
    Parse a mdBook SUMMARY.md file and return a list of SummaryItem objects.
    
    :param summary_path: Path to the SUMMARY.md file
    :return: A list of SummaryItem objects representing the structure
    """
    with open(summary_path, encoding='utf-8') as f:
        lines = f.readlines()
    
    summary_dir = summary_path.parent
    items = []
    current_part = None  # Track the current Part Title
    stack = [(items, -1)]  # (current_list, indent_level)
    in_numbered_section = False
    
    # Regex patterns
    # Matches: - [Title](path) or * [Title](path) or [Title](path)
    link_pattern = re.compile(r'^(\s*)[-*]?\s*\[([^\]]+)\]\(([^)]*)\)\s*$')
    # Matches: # Part Title (but not # Summary)
    part_title_pattern = re.compile(r'^#\s+(.+)$')
    # Matches: ---
    separator_pattern = re.compile(r'^-{3,}\s*$')
    
    for line in lines:
        # Skip empty lines
        if not line.strip():
            continue
        
        # Skip title line (# Summary)
        if line.strip().startswith('# Summary'):
            continue
        
        # Check for separator
        if separator_pattern.match(line):
            continue
        
        # Check for part title
        part_match = part_title_pattern.match(line)
        if part_match:
            part_title = part_match.group(1).strip()
            # Don't skip "Summary" as part title - only as document title
            item = SummaryItem(title=part_title, is_part_title=True)
            items.append(item)
            current_part = item
            # Reset stack to point to this part's children
            stack = [(current_part.children, -1)]
            in_numbered_section = True
            continue
        
        # Check for link (prefix chapter, numbered chapter, or suffix chapter)
        link_match = link_pattern.match(line)
        if link_match:
            indent = link_match.group(1)
            title = link_match.group(2)
            path_str = link_match.group(3)
            
            # Calculate indent level (2 spaces or 1 tab = 1 level, following mdBook convention)
            # Convert tabs to 4 spaces first, then count by 2s
            indent_level = len(indent.replace('\t', '    ')) // 2
            
            # Resolve path relative to SUMMARY.md
            file_path = None
            if path_str:  # Not a draft chapter
                file_path = (summary_dir / path_str).resolve()
            
            item = SummaryItem(title=title, path=file_path)
            
            # Determine if this is a numbered chapter (has list marker)
            has_list_marker = line.lstrip().startswith(('-', '*'))
            
            if has_list_marker:
                # This is a numbered chapter
                in_numbered_section = True
                
                # Find the appropriate parent based on indent level
                while len(stack) > 1 and stack[-1][1] >= indent_level:
                    stack.pop()
                
                # Add to current parent's children list
                current_list = stack[-1][0]
                current_list.append(item)
                
                # Push this item's children list onto stack for potential nested items
                stack.append((item.children, indent_level))
            else:
                # This is a prefix or suffix chapter (no list marker)
                # Add to top-level items, not to any part
                items.append(item)
                current_part = None
                # Reset stack for non-numbered chapters
                stack = [(items, -1)]
                in_numbered_section = False
    
    return items


def get_pages_from_summary(
    summary_path: Path,
    strip_header: bool = False,
    remove_text_newlines: bool = False,
    enable_relative_links: bool = False,
) -> List[Page]:
    """
    Generate a list of Page objects from a mdBook SUMMARY.md file.
    
    :param summary_path: Path to the SUMMARY.md file
    :param strip_header: Remove the top level header from pages
    :param remove_text_newlines: Remove single newlines in paragraphs
    :param enable_relative_links: Extract and replace relative links
    :return: A list of Page objects
    """
    summary_items = parse_summary_md(summary_path)
    pages = []
    
    # First, add the SUMMARY.md itself as the first page (if it's a Part Title without a file)
    # Find the first Part Title that doesn't have a path
    summary_page_item = None
    if summary_items and summary_items[0].is_part_title and not summary_items[0].path:
        summary_page_item = summary_items[0]
    
    def generate_toc_for_item(item: SummaryItem, indent_level: int = 0) -> str:
        """Generate table of contents markdown for an item and its children"""
        toc_lines = []
        indent = "  " * indent_level
        
        for child in item.children:
            # Add the child as a list item
            toc_lines.append(f"{indent}- {child.title}")
            
            # Recursively add nested children
            if child.children:
                nested_toc = generate_toc_for_item(child, indent_level + 1)
                toc_lines.append(nested_toc)
        
        return "\n".join(toc_lines)
    
    def process_item(
        item: SummaryItem,
        parent_title: Optional[str] = None,
        use_summary_content: bool = False
    ) -> None:
        """Recursively process summary items and create pages"""
        
        # Create page for this item
        if item.is_separator:
            return
        
        page_body = ""
        page_file_path = None
        page_attachments = []
        page_relative_links = []
        page_title = item.title
        
        # Special case: if this is the first Part Title without a path, use SUMMARY.md content
        if use_summary_content and item == summary_page_item:
            content_page = get_page_data_from_file_path(
                summary_path,
                strip_header=strip_header,
                remove_text_newlines=remove_text_newlines,
                enable_relative_links=enable_relative_links,
            )
            page_body = content_page.body
            page_file_path = content_page.file_path
            page_attachments = content_page.attachments
            page_relative_links = content_page.relative_links
            # Use SUMMARY.md title if available, otherwise use the Part Title from SUMMARY
            if content_page.title:
                page_title = content_page.title
        # If item has a path, load content from the markdown file
        elif item.path and item.path.exists():
            content_page = get_page_data_from_file_path(
                item.path,
                strip_header=strip_header,
                remove_text_newlines=remove_text_newlines,
                enable_relative_links=enable_relative_links,
            )
            page_body = content_page.body
            page_file_path = content_page.file_path
            page_attachments = content_page.attachments
            page_relative_links = content_page.relative_links
            # Use document title if available, otherwise use SUMMARY.md title
            if content_page.title:
                page_title = content_page.title
        elif item.is_part_title or not item.path:
            # Part titles or draft chapters: generate TOC if they have children
            if item.children:
                toc_content = generate_toc_for_item(item)
                # Parse the TOC markdown to Confluence format
                page_body = parse_page([toc_content]).body
            else:
                # Empty page if no children
                page_body = ""
        
        # Create the page
        page = Page(
            title=page_title,
            parent_title=parent_title,
            body=page_body,
            file_path=page_file_path,
            attachments=page_attachments,
            relative_links=page_relative_links,
        )
        pages.append(page)
        
        # Process children recursively
        for child in item.children:
            process_item(child, parent_title=page_title, use_summary_content=False)
    
    # Process all top-level items
    for item in summary_items:
        # Use SUMMARY.md content for the first item if applicable
        use_summary = (item == summary_page_item)
        process_item(item, parent_title=None, use_summary_content=use_summary)
    
    return pages
