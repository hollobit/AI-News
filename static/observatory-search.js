/* Literal query language: implicit AND, explicit AND/OR or |, quotes, exclusions. */
(() => {
'use strict';
const normalize=text=>String(text).normalize('NFKC').toLocaleLowerCase();
function parse(input){const text=input.trim();if(text.length>256)return {error:'검색어는 256자 이내로 입력해 주세요.',groups:[]};const tokens=[];let i=0;
 while(i<text.length){if(/\s/.test(text[i])){i++;continue;}if(text[i]==='|'){tokens.push({op:'OR'});i++;continue;}let negative=false;if(text[i]==='-'){negative=true;i++;}let value='',quoted=false;
 if(text[i]==='"'){quoted=true;i++;while(i<text.length&&text[i]!=='"'){if(text[i]==='\\'&&i+1<text.length)i++;value+=text[i++];}if(text[i]!=='"')return {error:'따옴표를 닫아 주세요.',groups:[]};i++;if(i<text.length&&!/\s|\|/.test(text[i]))return {error:'구문 사이에 공백을 넣어 주세요.',groups:[]};}
 else while(i<text.length&&!/\s|\|/.test(text[i]))value+=text[i++];
 if(!value)return {error:'검색할 키워드를 입력해 주세요.',groups:[]};if(!negative&&!quoted&&/^(AND|OR)$/i.test(value))tokens.push({op:value.toUpperCase()});else tokens.push({value:normalize(value),negative});
 }
 const groups=[[]];let expected=true,terms=0;
 for(const token of tokens){if(token.op){if(expected)return {error:'AND 또는 OR 앞뒤에 키워드를 입력해 주세요.',groups:[]};expected=true;if(token.op==='OR')groups.push([]);}else{groups.at(-1).push(token);expected=false;terms++;}}
 if(tokens.length&&expected)return {error:'AND 또는 OR 뒤에 키워드를 입력해 주세요.',groups:[]};if(terms>20)return {error:'키워드는 20개 이내로 입력해 주세요.',groups:[]};return {groups:tokens.length?groups:[],error:''};
}
function matches(query,labels){if(query.error)return false;if(!query.groups.length)return true;const texts=labels.map(normalize);return query.groups.some(group=>group.every(term=>term.negative?!texts.some(t=>t.includes(term.value)):texts.some(t=>t.includes(term.value))));}
globalThis.ObservatorySearch={parse,matches};
})();
