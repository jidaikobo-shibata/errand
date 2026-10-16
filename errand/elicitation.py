"""Bounded MCP form handling; unsupported schemas are never silently accepted."""
import json
import math
import re
from datetime import date, datetime
from urllib.parse import urlsplit


class UnsupportedForm(ValueError):
    pass


def confirmation_key(params):
    """Match exact confirmations, never infer an operation from prose."""
    if not isinstance(params, dict) or params.get('mode', 'form') != 'form':
        return None
    if not all(isinstance(params.get(key), str) and params[key].strip()
               for key in ('serverName', 'message')):
        return None
    try:
        properties, required = fields(params.get('requestedSchema'))
        if properties or required:
            return None
        identity = {key: value for key, value in params.items()
                    if key not in ('threadId', 'turnId', 'itemId', 'item')}
        return json.dumps(identity, ensure_ascii=True, sort_keys=True, allow_nan=False)
    except (ValueError, TypeError):
        return None


def browser_url(value):
    if not isinstance(value, str) or any(ord(c) < 33 or ord(c) == 127 for c in value) or '\\' in value:
        raise ValueError('安全に開けるURLではありません。')
    try:
        parsed = urlsplit(value)
        valid = (parsed.scheme in ('https', 'http') and parsed.hostname
                 and parsed.username is None and parsed.password is None and parsed.port != 0)
    except ValueError:
        valid = False
    if not valid:
        raise ValueError('HTTPまたはHTTPSのURLだけを開けます。')
    return value


def options(schema):
    if sum(key in schema for key in ('enum', 'oneOf', 'anyOf')) > 1:
        raise UnsupportedForm('複数の選択条件の組み合わせには対応していません。')
    if 'enum' in schema:
        values = schema['enum']
        names = schema.get('enumNames') or values
        if (not isinstance(values, list) or not isinstance(names, list) or len(values) != len(names)
                or not all(isinstance(v, str) for v in values + names)):
            raise UnsupportedForm('選択肢の形式に対応していません。')
        result = list(zip(values, names))
    elif 'oneOf' in schema or 'anyOf' in schema:
        choices = schema.get('oneOf', schema.get('anyOf'))
        if (not isinstance(choices, list) or not all(isinstance(v, dict)
                and set(v) <= {'const', 'title'} and isinstance(v.get('const'), str)
                and isinstance(v.get('title', v.get('const')), str) for v in choices)):
            raise UnsupportedForm('選択肢の形式に対応していません。')
        result = [(v['const'], v.get('title', v['const'])) for v in choices]
    else:
        return None
    if not result or len(result) > 100 or len({v for v, _ in result}) != len(result):
        raise UnsupportedForm('選択肢は重複のない100件以内にしてください。')
    return result


def fields(schema):
    if (not isinstance(schema, dict) or schema.get('type') != 'object'
            or set(schema) - {'type', 'properties', 'required', '$schema', 'additionalProperties', 'title', 'description'}):
        raise UnsupportedForm('このフォーム形式にはまだ対応していません。')
    properties = schema.get('properties', {})
    required = schema.get('required') or []
    if (not isinstance(properties, dict) or len(properties) > 50 or not isinstance(required, list)
            or not all(isinstance(key, str) and key in properties for key in required)):
        raise UnsupportedForm('フォームの項目数や必須項目の指定に対応していません。')
    for key, spec in properties.items():
        if not isinstance(spec, dict):
            raise UnsupportedForm('項目の形式に対応していません。')
        kind = spec.get('type')
        allowed = {'type', 'title', 'description', 'default'}
        if kind == 'string':
            allowed |= {'enum', 'enumNames', 'oneOf', 'minLength', 'maxLength', 'format'}
            options(spec)
            if spec.get('format') not in (None, 'email', 'uri', 'date', 'date-time'):
                raise UnsupportedForm('この文字列形式には対応していません。')
        elif kind in ('number', 'integer'):
            allowed |= {'minimum', 'maximum'}
        elif kind == 'array':
            allowed |= {'items', 'minItems', 'maxItems'}
            item = spec.get('items')
            if (not isinstance(item, dict) or set(item) - {'type', 'enum', 'anyOf'}
                    or item.get('type', 'string') != 'string' or options(item) is None):
                raise UnsupportedForm('複数選択以外の配列には対応していません。')
        elif kind != 'boolean':
            raise UnsupportedForm('この入力形式にはまだ対応していません。')
        if set(spec) - allowed:
            raise UnsupportedForm('未対応の入力条件が含まれています。')
        for bound in ('minimum', 'maximum', 'minLength', 'maxLength', 'minItems', 'maxItems'):
            value = spec.get(bound)
            if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                    or not math.isfinite(value) or (bound not in ('minimum', 'maximum')
                    and (not isinstance(value, int) or value < 0))):
                raise UnsupportedForm('入力範囲の指定が正しくありません。')
    return properties, set(required)


def content(schema, values):
    properties, required = fields(schema)
    result = {}
    for key, spec in properties.items():
        title = spec.get('title') or key
        raw = values.get(key)
        if raw is None or raw == '' and spec['type'] in ('integer', 'number'):
            if key in required:
                raise ValueError(f'「{title}」を入力・選択してください。')
            continue
        kind = spec['type']
        if kind == 'string':
            if not isinstance(raw, str) or '\x00' in raw:
                raise ValueError(f'「{title}」の文字列が正しくありません。')
            choices = options(spec)
            if raw == '' and key not in required and choices is None:
                continue
            if choices and raw not in [value for value, _ in choices]:
                raise ValueError(f'「{title}」を選択してください。')
            if len(raw) < (spec.get('minLength') or 0) or len(raw) > (spec.get('maxLength') if spec.get('maxLength') is not None else 100000):
                raise ValueError(f'「{title}」の文字数を確認してください。')
            fmt = spec.get('format')
            try:
                if fmt == 'email' and not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', raw):
                    raise ValueError()
                if fmt == 'uri' and not urlsplit(raw).scheme:
                    raise ValueError()
                if fmt == 'date':
                    date.fromisoformat(raw)
                if fmt == 'date-time':
                    parsed = datetime.fromisoformat(raw.replace('Z', '+00:00'))
                    if parsed.tzinfo is None: raise ValueError()
            except ValueError:
                raise ValueError(f'「{title}」の形式を確認してください。') from None
            value = raw
        elif kind in ('number', 'integer'):
            try:
                value = int(raw) if kind == 'integer' else float(raw)
                if isinstance(raw, bool) or not math.isfinite(value): raise ValueError()
                if kind == 'integer' and isinstance(raw, float) and raw != value: raise ValueError()
            except (ValueError, TypeError, OverflowError):
                raise ValueError(f'「{title}」には{ "整数" if kind == "integer" else "数値" }を入力してください。') from None
            if ((spec.get('minimum') is not None and value < spec['minimum'])
                    or (spec.get('maximum') is not None and value > spec['maximum'])):
                raise ValueError(f'「{title}」の数値が範囲外です。')
        elif kind == 'boolean':
            if not isinstance(raw, bool): raise ValueError(f'「{title}」を選択してください。')
            value = raw
        else:
            choices = [v for v, _ in options(spec['items'])]
            if (not isinstance(raw, list) or not all(v in choices for v in raw)
                    or len(set(raw)) != len(raw) or len(raw) < (spec.get('minItems') or 0)
                    or len(raw) > (spec.get('maxItems') if spec.get('maxItems') is not None else 100)):
                raise ValueError(f'「{title}」の選択数を確認してください。')
            if not raw and key not in required: continue
            value = raw
        result[key] = value
    return result
