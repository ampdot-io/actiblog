# actiblog

**note:** this version of the documentation is intended for developers or other
people comfortable with using command-line tools. stay tuned for a guide
oriented towards human blog post contributors

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
- 
