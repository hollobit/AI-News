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
  function highlight(root, query, options = {}) {
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
      if (
        !n.parentElement?.closest('mark,script,style,input,textarea,select,svg') &&
        !(
          options.question &&
          n.parentElement?.closest('button,.query-sentence,[data-highlight-skip]')
        )
      )
        nodes.push(n);
    }
    const graphemes = new Intl.Segmenter(undefined, { granularity: 'grapheme' });
    const sentences = options.question
      ? new Intl.Segmenter('ko', { granularity: 'sentence' })
      : null;
    for (const node of nodes) {
      const text = node.nodeValue;
      let normalized = '',
        offsets = [];
      const segments = graphemes.segment(text);
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
          // A question about AI must not mark the middle of "said" or "retail".
          const embedded =
            options.question &&
            ((/^[a-z0-9]/.test(term) && /[a-z0-9]/.test(normalized[at - 1] || '')) ||
              (/[a-z0-9]$/.test(term) && /[a-z0-9]/.test(normalized[at + term.length] || '')));
          if (!embedded) ranges.push([offsets[at][0], offsets[at + term.length - 1][1]]);
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
      function appendMarked(parent, begin, end) {
        let cursor = begin;
        for (const [a, b] of merged) {
          const start = Math.max(a, begin),
            stop = Math.min(b, end);
          if (start >= stop) continue;
          parent.append(document.createTextNode(text.slice(cursor, start)));
          const mark = document.createElement('mark');
          mark.className = 'search-highlight';
          mark.textContent = text.slice(start, stop);
          parent.append(mark);
          cursor = stop;
        }
        parent.append(document.createTextNode(text.slice(cursor, end)));
      }
      if (sentences && node.parentElement?.closest('p')) {
        for (const part of sentences.segment(text)) {
          const begin = part.index,
            end = begin + part.segment.length;
          if (!merged.some(([a, b]) => a < end && b > begin)) {
            fragment.append(document.createTextNode(part.segment));
            continue;
          }
          const sentence = document.createElement('span');
          sentence.className = 'query-sentence';
          appendMarked(sentence, begin, end);
          fragment.append(sentence);
        }
      } else appendMarked(fragment, 0, text.length);
      node.replaceWith(fragment);
    }
  }
  // Display-only terms: natural-language questions have a 2000-character
  // contract separate from the existing boolean search parser.
  const questionStop = new Set(
    (
      '최근 최신 관련 대한 대해 어떤 무엇 무엇인가요 무엇인가 무엇이며 어떻게 왜 알려주세요 알려 ' +
      '주세요 설명해주세요 설명해 설명 분석해주세요 분석해 분석 종합적으로 자세히 함께 ' +
      '해주세 해주세요 해줘 보여주세요 있는 있나요 있습니까 인가요 및 등 ' +
      'the a an and or of to in on for with from is are what how about tell me please'
    ).split(/\s+/)
  );
  const questionCache = new Map();
  function questionTerms(input) {
    const text = normalize(String(input || '').slice(0, 2000));
    if (questionCache.has(text)) return questionCache.get(text);
    const terms = new Set();
    for (const match of text.matchAll(/["“]([^"”]+)["”]/g)) {
      if (match[1].trim().length >= 2) terms.add(match[1].trim());
    }
    for (const word of text.match(/[a-z][a-z0-9]*(?:[.+-][a-z0-9]+|\+\+|#)?|[가-힣]{2,}|\d{4}/g) ||
      []) {
      const stem = /^[가-힣]+$/.test(word)
        ? word.replace(
            /(?:에서는|으로는|으로|에서|에게|까지|부터|보다|처럼|하고|이며|이라는|라는|에는|은|는|이|가|을|를|의|와|과|도|에)$/,
            ''
          )
        : word;
      if (questionStop.has(word) || questionStop.has(stem)) continue;
      terms.add(stem.length >= 2 ? stem : word);
    }
    const query = {
      groups: [[...terms].slice(0, 96).map((value) => ({ value, negative: false }))],
      error: '',
    };
    questionCache.set(text, query);
    if (questionCache.size > 8) questionCache.delete(questionCache.keys().next().value);
    return query;
  }
  function highlightQuestion(root, question) {
    highlight(root, questionTerms(question), { question: true });
  }
  globalThis.ObservatorySearch = { parse, matches, highlight, questionTerms, highlightQuestion };
})();
