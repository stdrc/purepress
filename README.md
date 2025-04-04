# PurePress

[![PyPI](https://img.shields.io/pypi/v/purepress.svg)](https://pypi.python.org/pypi/purepress/)

**PurePress** is a very simple static blog generator.

## Usage

```sh
pip install purepress

mkdir my-blog
cd my-blog

purepress init  # init the blog
git clone https://github.com/verilab/purepress-theme-default.git theme  # install a theme

purepress preview  # preview the blog
purepress build  # build the blog
```

See [richardchien/blog](https://github.com/richardchien/blog) for more.

## Minimality

```sh
❯ cloc purepress
       3 text files.
       3 unique files.
       3 files ignored.

github.com/AlDanial/cloc v 1.94  T=0.01 s (346.3 files/s, 74444.9 lines/s)
-------------------------------------------------------------------------------
Language                     files          blank        comment           code
-------------------------------------------------------------------------------
Python                           3             91             51            503
-------------------------------------------------------------------------------
SUM:                             3             91             51            503
-------------------------------------------------------------------------------
```
