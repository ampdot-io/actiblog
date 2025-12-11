# actiblog

## install

1. install [uv](https://docs.astral.sh/uv/#installation)
2. run `uv sync` to install Python dependencies
3. `git submodule update --init --recursive`
4. install [hugo](https://gohugo.io/installation/)

## workflow

### writing blog entries

run this:

```
hugo new content content/posts/my-post.md
```

## folder layout

- `inputs/` - input data
- `content/` - manually written posts for Hugo
- `data/` - input for automatic page creation by Hugo

## attribution

Thanks to Claude 3.7 Sonnet for OCR work, Gemini 3 and joysatisficer for programming help. ampdot performed key architectural design.