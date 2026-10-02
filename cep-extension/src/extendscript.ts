/**
 * Serialize a value as a literal that can be spliced into ExtendScript source.
 *
 * JSON.stringify (ES2019 "well-formed") leaves U+2028/U+2029 unescaped, but
 * ExtendScript is ES3, where both are line terminators: a raw one inside a
 * string literal is "Unterminated string constant" before the host function
 * ever runs. JSON.stringify only emits these characters inside strings, so
 * escaping them yields the same value.
 */
export function toExtendScriptLiteral(value: unknown): string {
    return JSON.stringify(value)
        .replace(/\u2028/g, '\\u2028')
        .replace(/\u2029/g, '\\u2029');
}
