import{n as e,t}from"./createLucideIcon-BhqHmOOf.js";var n=t(`ArrowLeft`,[[`path`,{d:`m12 19-7-7 7-7`,key:`1l729n`}],[`path`,{d:`M19 12H5`,key:`x3x0zl`}]]),r=e();function i(e){let t=e.replace(/\s+/g,` `).replace(/\s*,\s*/g,`, `).trim();return t?t.replace(/\b(with)\b/gi,`
$1`).replace(/\b(select)\b/gi,`
$1`).replace(/\b(from)\b/gi,`
$1`).replace(/\b((?:left|right|full|inner|outer|cross)\s+join|join)\b/gi,`
$1`).replace(/\b(where)\b/gi,`
$1`).replace(/\b(and|or)\b/gi,`
  $1`).replace(/\b(group\s+by)\b/gi,`
$1`).replace(/\b(having)\b/gi,`
$1`).replace(/\b(order\s+by)\b/gi,`
$1`).replace(/\b(limit|offset|union all|union)\b/gi,`
$1`).replace(/,\s+(?=(?:[^']*'[^']*')*[^']*$)/g,`,
  `).replace(/\s+,\s+/g,`, `).trim():``}function a(e){let t=e.match(/'(?:''|[^'])*'|"(?:\\.|[^"\\])*"|--.*?$|\/\*[\s\S]*?\*\/|\b(?:select|from|where|join|left|right|full|inner|outer|on|and|or|not|case|when|then|else|end|as|with|group|by|order|having|limit|offset|union|all|distinct|insert|update|delete|merge|into|values|set|is|null|in|exists|between|like|cast|coalesce|partition|over)\b|[-]?\d+(?:\.\d+)?|[(),.=<>+\-*/]/gim);if(!t)return e;let n=[],i=0;return t.forEach((t,a)=>{let s=e.indexOf(t,i);s<i||(s>i&&n.push(e.slice(i,s)),n.push((0,r.jsx)(`span`,{className:`sql-token ${o(t)}`,children:t},`${s}-${a}`)),i=s+t.length)}),i<e.length&&n.push(e.slice(i)),n}function o(e){return/^(--|\/\*)/.test(e)?`sql-comment`:/^['"]/.test(e)?`sql-string`:/^-?\d/.test(e)?`sql-number`:/^[a-z_]+$/i.test(e)?`sql-keyword`:`sql-punctuation`}export{a as n,n as r,i as t};