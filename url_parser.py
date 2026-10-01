"""Lossless HTTP URL spans; only surrounding prose punctuation is removed."""
import re
from urllib.parse import urlsplit

START=re.compile(r'https?://',re.I)
MARKDOWN=re.compile(r'\[[^\]\r\n]*\]\((?=https?://)',re.I)
PUNCT='.,;:!?\"\'…，。；：！？'


def valid(value):
    if not value or any(ord(c)<32 for c in value):return False
    try:
        parsed=urlsplit(value);port=parsed.port
        return parsed.scheme.lower() in ('http','https') and bool(parsed.hostname)
    except ValueError:return False


def clean_tail(value):
    value=value.rstrip(PUNCT)
    while value and value[-1] in ')]}':
        closer=value[-1];opener={')':'(',']':'[','}':'{'}[closer]
        if value.count(closer)<=value.count(opener):break
        value=value[:-1].rstrip(PUNCT)
    return value


def spans(text):
    text=str(text or '');explicit={m.end():m for m in MARKDOWN.finditer(text)}
    occupied=0
    for match in START.finditer(text):
        start=match.start()
        if start<occupied:continue  # A nested URL is part of a redirect parameter.
        end=match.end();depth=1;markdown=start in explicit
        while end<len(text) and not text[end].isspace() and text[end] not in '<>"`':
            char=text[end]
            if markdown:
                if char=='(':depth+=1
                elif char==')':
                    depth-=1
                    if depth==0:break
            end+=1
        value=text[start:end] if markdown and depth==0 else clean_tail(text[start:end])
        occupied=end
        if valid(value):yield start,start+len(value),value


def links(text):
    return list(dict.fromkeys(value for _,_,value in spans(text)))
