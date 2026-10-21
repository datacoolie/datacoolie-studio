import{n as e}from"./createLucideIcon-db8TuNKf.js";var t=e();function n(e){let t=e.replace(/\s+/g,` `).replace(/\s*,\s*/g,`, `).trim();return t?t.replace(/\b(with)\b/gi,`
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
  `).replace(/\s+,\s+/g,`, `).trim():``}function r(e){let n=e.match(/'(?:''|[^'])*'|"(?:\\.|[^"\\])*"|--.*?$|\/\*[\s\S]*?\*\/|\b(?:select|from|where|join|left|right|full|inner|outer|on|and|or|not|case|when|then|else|end|as|with|group|by|order|having|limit|offset|union|all|distinct|insert|update|delete|merge|into|values|set|is|null|in|exists|between|like|cast|coalesce|partition|over)\b|[-]?\d+(?:\.\d+)?|[(),.=<>+\-*/]/gim);if(!n)return e;let r=[],a=0;return n.forEach((n,o)=>{let s=e.indexOf(n,a);s<a||(s>a&&r.push(e.slice(a,s)),r.push((0,t.jsx)(`span`,{className:`sql-token ${i(n)}`,children:n},`${s}-${o}`)),a=s+n.length)}),a<e.length&&r.push(e.slice(a)),r}function i(e){return/^(--|\/\*)/.test(e)?`sql-comment`:/^['"]/.test(e)?`sql-string`:/^-?\d/.test(e)?`sql-number`:/^[a-z_]+$/i.test(e)?`sql-keyword`:`sql-punctuation`}export{r as n,n as t};