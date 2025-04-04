import os
import re
import functools
import xml.etree.ElementTree as etree
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

import yaml
import pytz
import toml
import markdown.extensions
import markdown.treeprocessors
from markdown import Markdown
from mdx_gfm import GithubFlavoredMarkdownExtension
from flask import (
    Flask,
    render_template,
    abort,
    redirect,
    url_for,
    Blueprint,
    send_from_directory,
    request,
    make_response,
)
from werkzeug.security import safe_join
from feedgen.feed import FeedGenerator
from html_toc import HtmlTocParser

# calculate some folder path
root_folder = os.getenv("PUREPRESS_INSTANCE", os.getcwd())
static_folder = os.path.join(root_folder, "static")
template_folder = os.path.join(root_folder, "theme", "templates")
theme_static_folder = os.path.join(root_folder, "theme", "static")
posts_folder = os.path.join(root_folder, "posts")
pages_folder = os.path.join(root_folder, "pages")
raw_folder = os.path.join(root_folder, "raw")

# load configurations
try:
    purepress_config = toml.load(os.path.join(root_folder, "purepress.toml"))
except FileNotFoundError:
    purepress_config = {"site": {}, "config": {}}
site, config = purepress_config["site"], purepress_config["config"]

app = Flask(
    __name__,
    instance_path=root_folder,
    template_folder=template_folder,
    static_folder=static_folder,
    instance_relative_config=True,
)

# handle static files for theme
theme_bp = Blueprint(
    "theme",
    __name__,
    static_url_path="/static/theme",
    static_folder=theme_static_folder,
)
app.register_blueprint(theme_bp)

# prepare markdown parser
class HookImageSrcProcessor(markdown.treeprocessors.Treeprocessor):
    def run(self, root: etree.Element):
        static_url = url_for("static", filename="")
        for el in root.iter("img"):
            src = el.get("src", "")
            if src.startswith("/static/"):
                el.set("src", re.sub(r"^/static/", static_url, src))


class HookLinkHrefProcessor(markdown.treeprocessors.Treeprocessor):
    @staticmethod
    def path_to_url(path: str) -> str:
        root = url_for("index").rstrip("/")
        url = path
        if path.startswith("/posts/"):
            # /posts/2021-08-23-hello-world.md -> /post/2021/08/23/hello-world/
            # /posts/2021-08-23-hello-world.md#anchor1 -> /post/2021/08/23/hello-world/#anchor1
            url = re.sub(r"^/posts/", f"{root}/post/", url)
            url = re.sub(r"-", "/", url, count=3)
            url = re.sub(r"\.md(#.*)?$", r"/\1", url)
        elif path.startswith("/pages/"):
            # /pages/about/ -> /about/
            # /pages/about/index.md -> /about/
            # /pages/foo/bar.md -> /foo/bar.html
            # /pages/foo/bar.md#anchor1 -> /foo/bar.html#anchor1
            url = re.sub(r"^/pages/", f"{root}/", url)
            url = re.sub(r"index\.md(#.*)?$", r"\1", url)
            url = re.sub(r"\.md(#.*)?$", r".html\1", url)
        elif path.startswith("/raw/"):
            # /raw/foo/baz.html -> /foo/baz.html
            url = re.sub(r"^/raw/", f"{root}/", url)
        return url

    def run(self, root: etree.Element):
        for el in root.iter("a"):
            href = el.get("href", "")
            if href.startswith("/"):
                el.set("href", self.path_to_url(href))


class Extension(markdown.extensions.Extension):
    def extendMarkdown(self, md) -> None:
        md.treeprocessors.register(HookImageSrcProcessor(), "hook-image-src", 5)
        md.treeprocessors.register(HookLinkHrefProcessor(), "hook-link-href", 5)


_md = Markdown(extensions=[GithubFlavoredMarkdownExtension(), Extension(), "footnotes"])


def markdown_convert(text: str) -> str:
    _md.reset()
    return _md.convert(text)


# inject site and config into template context
@app.context_processor
def inject_objects() -> Dict[str, Any]:
    return {"global": {"site": site, "config": config}}


def load_entry(fullpath: str, *, meta_only: bool, parse_toc: bool) -> Optional[Dict[str, Any]]:
    # read frontmatter and content
    frontmatter, content = "", ""
    try:
        with open(fullpath, mode="r", encoding="utf-8") as f:
            firstline = f.readline().strip()
            remained = f.read().strip()
            if firstline == "---":
                frontmatter, remained = remained.split("---", maxsplit=1)
                content = remained.strip()
            else:
                content = "\n\n".join([firstline, remained])
    except FileNotFoundError:
        return None
    # construct the entry object
    entry: Dict[str, Any] = yaml.load(frontmatter, Loader=yaml.FullLoader) or {}
    # ensure datetime fields are real datetime
    for k in ("created", "updated"):
        if isinstance(entry.get(k), date) and not isinstance(entry.get(k), datetime):
            entry[k] = datetime.combine(entry[k], datetime.min.time())
    # ensure tags and categories are lists
    for k in ("categories", "tags"):
        if isinstance(entry.get(k), str):
            entry[k] = [entry[k]]
    # if should, convert markdown content to html
    if not meta_only:
        entry["content"] = markdown_convert(content)
        if parse_toc:
            parser = HtmlTocParser()
            parser.feed(entry["content"])
            entry["content"] = parser.html
            depth = entry.get("toc_depth", config.get("toc_depth")) or 0
            entry["toc"] = parser.toc(depth=depth)
            entry["toc_html"] = parser.toc_html(depth=depth)
    return entry


def load_post(filename: str, *, meta_only: bool = False, parse_toc: bool = False) -> Optional[Dict[str, Any]]:
    # parse the filename (yyyy-MM-dd-post-title.md)
    try:
        year, month, day, name = os.path.splitext(filename)[0].split("-", maxsplit=3)
        year, month, day = int(year), int(month), int(day)
    except ValueError:
        return None
    # load post entry
    fullpath = safe_join(posts_folder, filename)
    if fullpath is None:
        return None
    post = load_entry(fullpath, meta_only=meta_only, parse_toc=parse_toc)
    if post is None:  # note that post may be {}
        return None
    # add some fields
    post["filename"] = filename
    post["url"] = url_for(
        "post",
        year=f"{year:0>4d}",
        month=f"{month:0>2d}",
        day=f"{day:0>2d}",
        name=name,
    )
    # ensure *title* field
    if "title" not in post:
        post["title"] = " ".join(name.split("-"))
    # ensure *created* field
    if "created" not in post:
        post["created"] = datetime(year=year, month=month, day=day)
    return post


def load_posts(*, meta_only: bool = False) -> List[Dict[str, Any]]:
    try:
        post_files = os.listdir(posts_folder)
    except FileNotFoundError:
        return []

    def gen_posts():
        for post_file in post_files:
            if not post_file.endswith(".md"):
                continue
            yield load_post(post_file, meta_only=meta_only)

    posts = list(filter(lambda x: x and not x.get("hide", False), gen_posts()))
    posts.sort(key=lambda x: x.get("created"), reverse=True)
    return posts


def load_page(rel_url: str, *, parse_toc: bool = False) -> Optional[Dict[str, Any]]:
    # convert relative url to full file path
    pathnames = rel_url.split("/")
    fullpath = safe_join(pages_folder, *pathnames)
    if fullpath is None:
        return None
    if fullpath.endswith(os.path.sep):  # /foo/bar/
        fullpath = os.path.join(fullpath, "index.md")
    elif fullpath.endswith(".html"):  # /foo/bar.html
        fullpath = os.path.splitext(fullpath)[0] + ".md"
    else:  # /foo/bar
        fullpath += ".md"
    # load page entry
    page = load_entry(fullpath, meta_only=False, parse_toc=parse_toc)
    if page is None:
        return None
    page["url"] = url_for("page", rel_url=rel_url)
    # ensure *title* field
    if "title" not in page:
        name = os.path.splitext(os.path.basename(fullpath))[0]
        page["title"] = " ".join(name.split("-"))
    return page


def templated(template: str) -> Callable:
    if not template.endswith(".html"):
        template += ".html"

    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            res = func(*args, **kwargs)
            if isinstance(res, dict):
                return render_template([f"custom/{template}", template], **res)
            return res

        return wrapper

    return decorator


@app.route("/")
def index():
    # the logic is the same as /page/1/, just reuse it
    return index_page(1, from_index=True)


@app.route("/page/<int:page_num>/")
@templated("index")
def index_page(page_num, *, from_index: bool = False):
    # do some calculation and handle unexpected cases
    posts_per_page = config["posts_per_index_page"]
    posts = load_posts(meta_only=True)  # just load meta data quickly
    post_count = len(posts)
    page_count = (post_count + posts_per_page - 1) // posts_per_page
    if page_num == 1 and not from_index:
        # redirect /page/1/ to /
        return redirect(url_for("index"), 302)
    if page_num < 1 or page_num > page_count:
        abort(404)

    # prepare pager links
    prev_url, next_url = None, None
    if page_num == 2:
        prev_url = url_for("index")
    elif page_num > 2:
        prev_url = url_for("index_page", page_num=page_num - 1)
    if page_num < page_count:
        next_url = url_for("index_page", page_num=page_num + 1)

    # load posts in the specified range
    begin = (page_num - 1) * posts_per_page
    end = min(post_count, begin + posts_per_page)
    posts_to_render = []
    for i in range(begin, end):
        posts_to_render.append(load_post(posts[i]["filename"]))
    return {
        "entries": posts_to_render,
        "pager": {"prev_url": prev_url, "next_url": next_url},
    }


@app.route("/post/<year>/<month>/<day>/<name>/")
@templated("post")
def post(year: str, month: str, day: str, name: str):
    # use secure_filename to avoid filename attacks
    post = load_post(f"{year}-{month}-{day}-{name}.md", parse_toc=True)
    if not post:
        abort(404)
    return {"entry": post}


@app.route("/archive/")
@templated("archive")
def archive():
    posts = load_posts(meta_only=True)
    return {"entries": posts, "archive": {"type": "Archive", "name": "All"}}


@app.route("/category/<name>/")
@templated("archive")
def category(name: str):
    posts = list(filter(lambda p: name in p.get("categories", []), load_posts(meta_only=True)))
    return {"entries": posts, "archive": {"type": "Category", "name": name}}


@app.route("/tag/<name>/")
@templated("archive")
def tag(name: str):
    posts = list(filter(lambda p: name in p.get("tags", []), load_posts(meta_only=True)))
    return {"entries": posts, "archive": {"type": "Tag", "name": name}}


@app.route("/<path:rel_url>")
@templated("page")
def page(rel_url: str):
    page = load_page(rel_url, parse_toc=True)
    if not page:
        if rel_url.endswith("/"):
            rel_url += "/index.html"
        return send_from_directory(raw_folder, rel_url)
    return {"entry": page}


@app.errorhandler(404)
@app.route("/404.html")
def page_not_found(e=None):
    return render_template("404.html"), 404


def s2tz(tz_str):
    m = re.match(r"UTC([+|-]\d{1,2}):(\d{2})", tz_str)
    if m:  # in format 'UTC±[hh]:[mm]'
        delta_h = int(m.group(1))
        delta_m = int(m.group(2)) if delta_h >= 0 else -int(m.group(2))
        return timezone(timedelta(hours=delta_h, minutes=delta_m))
    try:  # in format 'Asia/Shanghai'
        return pytz.timezone(tz_str)
    except pytz.UnknownTimeZoneError:
        return None


@app.route("/feed.xml")
def feed():
    root_url = request.url_root.rstrip("/")
    home_full_url = root_url + url_for("index")
    feed_full_url = root_url + url_for("feed")
    site_tz = s2tz(site.get("timezone", "")) or timezone(timedelta())
    # set feed info
    feed_gen = FeedGenerator()
    feed_gen.id(home_full_url)
    feed_gen.title(site.get("title", ""))
    feed_gen.subtitle(site.get("subtitle", ""))
    if "author" in site:
        feed_gen.author(name=site["author"])
    feed_gen.link(href=home_full_url, rel="alternate")
    feed_gen.link(href=feed_full_url, rel="self")
    # add feed entries
    posts = load_posts(meta_only=True)[:10]
    for i in range(len(posts)):
        p = load_post(posts[i]["filename"])
        if not p:
            continue
        feed_entry = feed_gen.add_entry()
        feed_entry.id(root_url + p["url"])
        feed_entry.link(href=root_url + p["url"])
        feed_entry.title(p["title"])
        feed_entry.content(p["content"], type="CDATA")
        feed_entry.published(p["created"].replace(tzinfo=site_tz))
        feed_entry.updated(p.get("updated", p["created"]).replace(tzinfo=site_tz))
        if "author" in p:
            feed_entry.author(name=p["author"])
    # make http response
    resp = make_response(feed_gen.rss_str(pretty=True))
    resp.content_type = "application/rss+xml"
    return resp
