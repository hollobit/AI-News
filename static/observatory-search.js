/* Literal query language: implicit AND, explicit AND/OR or |, quotes, exclusions. */
(() => {
  'use strict';
  const normalize = (text) => String(text).normalize('NFKC').toLocaleLowerCase();
  function parse(input) {
    const text = input.trim();
    if (text.length > 256) return { error: '검색어는 256자 이내로 입력해 주세요.', groups: [] };
    const tokens = [];
    let i = 0;
    while (i < text.length) {
      if (/\s/.test(text[i])) {
        i++;
        continue;
      }
      if (text[i] === '|') {
        tokens.push({ op: 'OR' });
        i++;
        continue;
      }
      let negative = false;
      if (text[i] === '-') {
        negative = true;
        i++;
      }
      let value = '',
        quoted = false;
      if (text[i] === '"') {
        quoted = true;
        i++;
        while (i < text.length && text[i] !== '"') {
          if (text[i] === '\\' && i + 1 < text.length) i++;
          value += text[i++];
        }
        if (text[i] !== '"') return { error: '따옴표를 닫아 주세요.', groups: [] };
        i++;
        if (i < text.length && !/\s|\|/.test(text[i]))
          return { error: '구문 사이에 공백을 넣어 주세요.', groups: [] };
      } else while (i < text.length && !/\s|\|/.test(text[i])) value += text[i++];
      if (!value) return { error: '검색할 키워드를 입력해 주세요.', groups: [] };
      if (!negative && !quoted && /^(AND|OR)$/i.test(value))
        tokens.push({ op: value.toUpperCase() });
      else tokens.push({ value: normalize(value), negative });
    }
    const groups = [[]];
    let expected = true,
      terms = 0;
    for (const token of tokens) {
      if (token.op) {
        if (expected) return { error: 'AND 또는 OR 앞뒤에 키워드를 입력해 주세요.', groups: [] };
        expected = true;
        if (token.op === 'OR') groups.push([]);
      } else {
        groups.at(-1).push(token);
        expected = false;
        terms++;
      }
    }
    if (tokens.length && expected)
      return { error: 'AND 또는 OR 뒤에 키워드를 입력해 주세요.', groups: [] };
    if (terms > 20) return { error: '키워드는 20개 이내로 입력해 주세요.', groups: [] };
    return { groups: tokens.length ? groups : [], error: '' };
  }
  function matches(query, labels) {
    if (query.error) return false;
    if (!query.groups.length) return true;
    const texts = labels.map(normalize);
    return query.groups.some((group) =>
      group.every((term) =>
        term.negative
          ? !texts.some((t) => t.includes(term.value))
          : texts.some((t) => t.includes(term.value))
      )
    );
  }
  // Build marks from text nodes only: query/source strings are never HTML.
  function highlight(root, query) {
    const parsed = typeof query === 'string' ? parse(query) : query;
    if (parsed.error) return;
    const terms = [
      ...new Set(
        parsed.groups
          .flat()
          .filter((t) => !t.negative)
          .map((t) => t.value)
      ),
    ];
    if (!terms.length) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    const nodes = [];
    while (walker.nextNode()) {
      const n = walker.currentNode;
      if (!n.parentElement?.closest('mark,script,style,input,textarea,select,svg')) nodes.push(n);
    }
    for (const node of nodes) {
      const text = node.nodeValue;
      let normalized = '',
        offsets = [];
      const segments = new Intl.Segmenter(undefined, { granularity: 'grapheme' }).segment(text);
      for (const part of segments) {
        const value = normalize(part.segment);
        normalized += value;
        for (let i = 0; i < value.length; i++)
          offsets.push([part.index, part.index + part.segment.length]);
      }
      const ranges = [];
      for (const term of terms) {
        let at = normalized.indexOf(term);
        while (at >= 0) {
          ranges.push([offsets[at][0], offsets[at + term.length - 1][1]]);
          at = normalized.indexOf(term, at + term.length);
        }
      }
      if (!ranges.length) continue;
      ranges.sort((a, b) => a[0] - b[0] || b[1] - a[1]);
      const merged = [];
      for (const range of ranges) {
        const last = merged.at(-1);
        if (last && range[0] <= last[1]) last[1] = Math.max(last[1], range[1]);
        else merged.push([...range]);
      }
      const fragment = document.createDocumentFragment();
      let end = 0;
      for (const [start, stop] of merged) {
        fragment.append(document.createTextNode(text.slice(end, start)));
        const mark = document.createElement('mark');
        mark.className = 'search-highlight';
        mark.textContent = text.slice(start, stop);
        fragment.append(mark);
        end = stop;
      }
      fragment.append(document.createTextNode(text.slice(end)));
      node.replaceWith(fragment);
    }
  }
  globalThis.ObservatorySearch = { parse, matches, highlight };
})();
