"""OpenAPI 3 document derived from the live API route catalog."""
import re


def obj(properties, required=()):
    result = {'type': 'object', 'properties': properties, 'additionalProperties': False}
    if required:
        result['required'] = list(required)
    return result


S = {'type': 'string'}
I = {'type': 'integer', 'minimum': 1}
B = {'type': 'boolean'}
U = {'type': 'string', 'format': 'uuid'}
DATE = {'type': 'string', 'format': 'date-time', 'description': 'ISO 8601; include an offset, e.g. +08:00. Naive dates use Asia/Shanghai.'}

SCHEMAS = {
    'TokenIssueRequest': obj({'name': {**S, 'maxLength': 100},
        'expires_in': {'type': 'integer', 'minimum': 0, 'maximum': 3153600000, 'default': 604800,
                       'description': 'Lifetime in seconds; 0 means permanent.'}}),
    'TokenRequest': obj({'username': S, 'password': {'type': 'string', 'format': 'password'}, 'name': {**S, 'maxLength': 100},
        'expires_in': {'type': 'integer', 'minimum': 0, 'maximum': 3153600000, 'default': 604800,
                       'description': 'Lifetime in seconds; 0 means permanent. expires_at is null for permanent credentials.'}}, ['username', 'password']),
    'PasswordRequest': obj({key: {'type': 'string', 'format': 'password'} for key in ['old_password', 'new_password1', 'new_password2']}, ['old_password', 'new_password1', 'new_password2']),
    'KeyRequest': obj({'name': S, 'public_key': S}, ['name', 'public_key']),
    'KeyUpdate': obj({'name': S, 'enabled': B}),
    'NodeRequest': obj({'name': S, 'transport': {'type': 'string', 'enum': ['local', 'ssh']}, 'ssh_port': I,
        'ssh_user': S, 'identity': S, 'architecture': {'type': 'string', 'enum': ['amd64', 'arm64']}, 'enabled': B, 'maintenance': B}),
    'TaskRequest': obj({'node': I, 'purpose': S, 'mode': {'type': 'string', 'enum': ['interactive', 'batch'], 'default': 'interactive'},
        'image': U, 'source_kind': {'type': 'string', 'enum': ['image', 'environment']},
        'source_environment':{**U,'description':'Own running development environment, with or without GPUs. Save its image, release it, then run the requested command.'},
        'gpu': {'type': 'integer', 'minimum': 0, 'description':'Number of distinct physical GPUs'},
        'gpu_requests':{'type':'array','maxItems':64,'items':{'$ref':'#/components/schemas/GPURequest'},
            'description':'One entry per distinct physical GPU, in CUDA device order. gpu may be omitted and is inferred from this list.'},
        'cpu': I, 'memory_gib': I, 'workspace_gib':{**I,'description':'When using an environment, omitted means its existing work volume size. A changed size creates a new volume and copies data after the source stops; the destination must fit all data.'},
        'starts_at': DATE, 'ends_at': DATE, 'command': S, 'working_dir': {**S, 'default': '/workspace'},
        'environment': {'type': 'object', 'additionalProperties': S}, 'notes': S}, ['purpose']),
    'ReviewRequest': obj({'action': {'type': 'string', 'enum': ['approve', 'reject', 'stop', 'extend']},
        'reason': S, 'priority': {'type': 'integer', 'enum': [0, 100]}, 'ends_at': DATE}, ['action']),
    'BuildRequest': obj({'node': I, 'name': S, 'dockerfile': S, 'context_git': {'type': 'string', 'format': 'uri'}}, ['name', 'dockerfile']),
    'MemberRequest': obj({'username': S, 'first_name': S, 'email': {'type': 'string', 'format': 'email'},
        'group': I, 'home_quota_gib': I, 'password': {'type': 'string', 'format': 'password'}, 'is_active': B,
        'is_staff': {**B, 'description': 'Editable by a superuser, matching the member form.'}}),
    'GroupRequest': obj({'name': S, 'unlimited': {**B, 'description': 'true clears all stored quota values; unlimited group responses omit quota fields. Switching to false requires every quota.'}, 'make_default': B,
        **{key: {**I, 'nullable': True} for key in ['cpu', 'memory_gib', 'workspace_gib', 'home_gib', 'image_gib', 'concurrent_tasks', 'interactive_hours']},
        'gpu_compute_percent': {'type': 'integer', 'minimum': 0, 'nullable': True,'description':'Total compute quota in percentage points; 150 permits 100% + 50% across GPUs'},
        'gpu_memory_gib':{'type':'integer','minimum':0,'nullable':True,'description':'Total VRAM quota in GiB'},
        'gpu_compute_per_card_percent':{'type':'integer','minimum':0,'maximum':100,'nullable':True,'description':'Maximum compute share of each requested GPU'},
        'gpu_memory_per_card_gib':{'type':'integer','minimum':0,'nullable':True,'description':'Maximum VRAM of each requested GPU in GiB'}}),
    'SettingsRequest': obj({key: I for key in ['workspace_retention_days', 'snapshot_hours', 'failed_snapshot_hours']}),
    'EmptyRequest': obj({}),
    'GPURequest':obj({'compute_percent':{'oneOf':[{'type':'integer','minimum':2,'maximum':100},{'type':'string','enum':['max']}]},
        'memory_gib':{'oneOf':[I,{'type':'string','enum':['max']}] }},['compute_percent','memory_gib']),
    'ResolvedGPURequest':obj({'compute_percent':{'type':'integer','minimum':2,'maximum':100},'memory_gib':I},['compute_percent','memory_gib']),
    'GPURequestLimits':obj({'compute_per_card_percent':{'type':'integer','minimum':0,'maximum':100},
        'memory_per_card_gib':{'type':'integer','minimum':0},
        'compute_total_percent':{'type':'integer','minimum':0,'nullable':True},
        'memory_total_gib':{'type':'integer','minimum':0,'nullable':True},
        'devices':{'type':'array','items':obj({'memory_gib':I,'sm_count':I},['memory_gib','sm_count'])}}),
    'GPUAllocation': obj({'uuid':S,'index':{'type':'integer','minimum':0},'sm_count':I,
        'slot':{'type':'integer','minimum':0,'description':'Zero-based request and CUDA device index'},
        'compute_percent':{'type':'integer','minimum':2,'maximum':100},
        'actual_compute_percent':{'type':'number','minimum':0,'maximum':100},'memory_mib':I,'partition':S}),
    'GPUDevice': obj({'node_id': I, 'node_name': S, 'uuid':S,'index':{'type':'integer','minimum':0},'name':S,'pci':S,'sm_count':I,
        'memory_mib':I,'usable_memory_mib':I,'ready':B,
        **{k:{'type':'integer','minimum':0} for k in ['used_compute_percent','used_sm_count','used_memory_mib','available_compute_percent']},
        **{k:{'type':'number','minimum':0} for k in ['available_memory_gib','used_memory_gib','memory_total_gib']}}),
    'GPUInventoryResponse': obj({'data':obj({'devices':{'type':'array','items':{'$ref':'#/components/schemas/GPUDevice'}},'fresh':B,
        'request_limits':{'$ref':'#/components/schemas/GPURequestLimits'}})},['data']),
    'CapacityMetric':{'type':'object','nullable':True,'properties':{
        'available':{'type':'number','minimum':0},'total':{'type':'number','minimum':0}},'additionalProperties':True},
    'RequestCapacity':obj({'fresh':B,
        'cpu':{'$ref':'#/components/schemas/CapacityMetric'},
        'memory_gib':{'$ref':'#/components/schemas/CapacityMetric'},
        'storage_gib':{'type':'number','minimum':0},
        'workspace_gib':obj({key:{'type':'integer','minimum':0,'nullable':True,
            'description':'null means unlimited.'} for key in ['available','total']}),
        'gpu_quota':{'type':'object','nullable':True,'properties':{
            'compute_percent':{'type':'integer','minimum':0},'memory_gib':{'type':'integer','minimum':0}}},
        'gpus':{'type':'array','items':obj({'node_id':I,'node_name':S,'index':{'type':'integer','minimum':0},'ready':B,
            'compute_percent':{'type':'number','minimum':0,'maximum':100},
            'memory_gib':{'type':'number','minimum':0},'memory_total_gib':{'type':'number','minimum':0}})}}),
    'Node': obj({'id': I, 'name': S, 'architecture': {'type': 'string', 'enum': ['amd64', 'arm64']},
        'transport': {'type': 'string', 'enum': ['local', 'ssh']}, 'enabled': B, 'maintenance': B,
        'state': S, 'state_label': S, 'last_seen': {**DATE, 'nullable': True}, 'error': S,
        'ssh_port': {**I, 'maximum': 65535, 'description': 'Administrator only.'},
        'ssh_user': {**S, 'description': 'Administrator only.'}, 'identity': {**S, 'description': 'Administrator only; key filename.'}},
        ['id', 'name', 'architecture', 'transport', 'enabled', 'maintenance', 'state', 'state_label', 'last_seen', 'error']),
    'NodeResponse': obj({'data': {'$ref': '#/components/schemas/Node'}}, ['data']),
    'NodeListResponse': obj({'data': obj({'items': {'type': 'array', 'items': {'$ref': '#/components/schemas/Node'}},
        'count': {'type': 'integer', 'minimum': 0}, 'page': I, 'page_size': I, 'next_page': {**I, 'nullable': True}})}, ['data']),
    'NodeResources': obj({'id': I, 'name': S, 'state': S, 'state_label': S,
        'resources': {'type': 'object', 'nullable': True, 'additionalProperties': True},
        'gpus': {'type': 'array', 'items': {'$ref': '#/components/schemas/GPUDevice'}},
        'model': S, 'gpu_available': {'type': 'number', 'minimum': 0},
        'storage_tib': {'type': 'number', 'minimum': 0, 'nullable': True}}),
    'ResourcesResponse':obj({'data':{'type':'object','properties':{
        'nodes': {'type': 'array', 'items': {'$ref': '#/components/schemas/NodeResources'},
                  'description': 'Dashboard resource groups; node=ID limits this list to that node.'},
        'request_capacity':{'$ref':'#/components/schemas/RequestCapacity'}},'additionalProperties':True}},['data']),
    'TimelineRow': obj({'id': S, 'node_id': I, 'name': S, 'detail': S}),
    'TimelineEntry': {'type': 'object', 'properties': {'node_id': I, 'id': {**U, 'nullable': True},
        'title': S, 'url': {**S, 'nullable': True}, 'state': {'type': 'string', 'enum': ['reserved', 'running', 'finished']},
        'start': DATE, 'end': {**DATE, 'nullable': True}, 'left': {'type': 'number'}, 'width': {'type': 'number'},
        'gpu': {'type': 'integer', 'minimum': 0}, 'cpu': I, 'memory': I}, 'additionalProperties': True},
    'TimelineQueuedTask': obj({'node_id': I, 'id': {**U, 'nullable': True}, 'title': S, 'url': {**S, 'nullable': True},
        'status': S, 'status_label': S, 'gpu': {'type': 'integer', 'minimum': 0}, 'cpu': I, 'memory_gib': I},
        ['node_id', 'id', 'title', 'url', 'status', 'status_label', 'gpu', 'cpu', 'memory_gib']),
    'TimelineGroup': obj({'id': I, 'name': S, 'gpu_count': {'type': 'integer', 'minimum': 0},
        'rows': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineRow'}},
        'blocks': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineEntry'}},
        'entries': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineEntry'}},
        'queued': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineQueuedTask'},
                   'description': 'Waiting tasks eligible to start now, ordered by priority then submission time. Empty on other dates. Other users see anonymous IDs and titles.'}},
        ['id', 'name', 'gpu_count', 'rows', 'blocks', 'entries', 'queued']),
    'TimelineResponse': obj({'data': obj({'date': {'type': 'string', 'format': 'date'},
        'now': {'type': 'number', 'nullable': True, 'description': 'Current time as a percentage of the selected day.'},
        'rows': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineRow'}},
        'blocks': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineEntry'}},
        'groups': {'type': 'array', 'items': {'$ref': '#/components/schemas/TimelineGroup'}}})}, ['data']),
    'TaskGPUResources': {'type':'object','properties':{
        'gpu':{'type':'integer','minimum':0},'gpu_requests':{'type':'array','items':{'$ref':'#/components/schemas/ResolvedGPURequest'}},
        'gpu_compute_total':{'type':'integer','minimum':0},
        'gpu_memory_total_gib':{'type':'integer','minimum':0},
        'gpu_allocations':{'type':'array','items':{'$ref':'#/components/schemas/GPUAllocation'}}},'additionalProperties':True},
    'TaskResponse': obj({'data':{'$ref':'#/components/schemas/TaskGPUResources'}},['data']),
    'SSHConnection':obj({'host':S,'port':I,'user':S,'alias':S,'command':S,'config':S}),
    'AdministratorSSHAccess':obj({'ready':B,'sudo':B,'keys':{'type':'array',
        'items':obj({'id':I,'name':S,'fingerprint':S})}}),
    'ConnectionResponse':obj({'data':obj({'status':S,
        'connection':{'allOf':[{'$ref':'#/components/schemas/SSHConnection'}],'nullable':True},
        'administrator_access':{'$ref':'#/components/schemas/AdministratorSSHAccess'}})},['data']),
    'GroupDelete': obj({'destination': I}),
    'PerformanceMetrics': {'type': 'object', 'properties': {
        'version': {'type': 'integer', 'enum': [1]}, 'sampled_at': DATE, 'interval_seconds': I,
        'cpu': obj({'percent': {'type': 'number', 'minimum': 0, 'maximum': 100, 'nullable': True}, 'cores': I,
            'load': {'type': 'array', 'items': {'type': 'number'}}, 'temperature_c': {'type': 'number', 'nullable': True},
            'temperature_sensors': {'type': 'array', 'items': obj({'name': S, 'temperature_c': {'type': 'number'}})}}),
        'memory': obj({key: {'type': 'number', 'minimum': 0} for key in ['total_gib', 'used_gib', 'available_gib', 'percent']}),
        'gpus': {'type': 'array', 'items': obj({'index': {'type': 'integer', 'minimum': 0}, 'uuid': S, 'name': S,
            **{key: {'type': 'number', 'minimum': 0, 'nullable': True} for key in ['utilization_percent', 'memory_used_mib',
                'memory_total_mib', 'memory_percent', 'temperature_c', 'power_w', 'power_limit_w', 'fan_percent']}})},
        'disks': {'type': 'array', 'items': obj({'name': S, **{key: {'type': 'number', 'minimum': 0}
            for key in ['total_gib', 'used_gib', 'available_gib', 'percent']}})},
        'network': {'type': 'array', 'items': obj({'name': S, 'rx_mib_s': {'type': 'number', 'minimum': 0, 'nullable': True},
            'tx_mib_s': {'type': 'number', 'minimum': 0, 'nullable': True}})},
        'errors': {'type': 'array', 'items': S}}, 'additionalProperties': False},
    'PerformanceResponse': obj({'data': obj({'refresh_seconds': I, 'nodes': {'type': 'array', 'items': obj({
        'id': I, 'name': S, 'architecture': S, 'state': S, 'fresh': B, 'status_label': S,
        'sampled_at': {**DATE, 'nullable': True},
        'metrics': {'allOf': [{'$ref': '#/components/schemas/PerformanceMetrics'}], 'nullable': True}})}})}, ['data']),
    'Response': {'type': 'object', 'required': ['data'], 'properties': {'data': {}}},
    'Error': {'type': 'object', 'required': ['error'], 'properties': {'error': obj({'code': S, 'message': S, 'fields': {'type': 'object'}}, ['code', 'message'])}},
}


def openapi(request):
    from .api_urls import ROUTES
    paths = {}
    for route, view, summary, schema in ROUTES:
        parameters = []
        for converter, name in re.findall(r'<([^:>]+):([^>]+)>', route):
            datatype = I if converter == 'int' else U if converter == 'uuid' else {**S, 'enum': ['keep', 'recycle']}
            parameters.append({'name': name, 'in': 'path', 'required': True, 'schema': datatype})
        path = '/'+re.sub(r'<[^:>]+:([^>]+)>', r'{\1}', route)
        operations = {}
        for method in view.api_methods:
            requires_staff = view.api_staff or (view.__name__ == 'volume' and method == 'DELETE') or (view.__name__ in {'node', 'nodes'} and method != 'GET')
            op = {'summary': summary, 'operationId': view.__name__+'_'+method.lower(),
                  'tags': ['admin' if requires_staff else route.split('/')[0] or 'discovery'],
                  'x-required-role': 'public' if method in view.api_public else 'staff' if requires_staff else 'account',
                  'security': [] if method in view.api_public else [{'bearerAuth': []}],
                  'parameters': list(parameters),
                  'responses': {'200': {'description': 'Successful response', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Response'}}}},
                                '400': {'description': 'Invalid request', 'content': {'application/json': {'schema': {'$ref': '#/components/schemas/Error'}}}},
                                '401': {'description': 'Missing, expired or revoked Bearer token'}, '403': {'description': 'Insufficient permissions'},
                                '404': {'description': 'Object not found'}, '409': {'description': 'Conflict or idempotency mismatch'}}}
            if method == 'GET' and view.__name__ in {'nodes', 'tokens', 'tasks', 'containers', 'keys', 'images', 'builds', 'snapshots', 'volumes', 'members', 'groups', 'storage', 'managed_images', 'audits', 'datasets'}:
                op['parameters'] += [{'name': name, 'in': 'query', 'schema': {**I, 'maximum': 100} if name == 'page_size' else I} for name in ['page', 'page_size']]
            queries = {'tasks': ['mode', 'status', 'purpose', 'scope'], 'images': ['available', 'kind'], 'timeline': ['date'],
                       'logs': ['tail'], 'datasets': ['path', 'q'], 'download': ['path'], 'members': ['q'], 'audits': ['action'],
                       'containers':['q','status','mode']}
            if view.__name__ in {'resources', 'gpus', 'timeline', 'tasks', 'images', 'builds', 'snapshots', 'volumes', 'datasets', 'download', 'containers', 'storage', 'managed_images', 'performance'}:
                queries.setdefault(view.__name__, []).append('node')
            if method == 'GET':
                op['parameters'] += [{'name': name, 'in': 'query', 'schema': I if name == 'node' else S} for name in queries.get(view.__name__, [])]
                response_schema={'resources':'ResourcesResponse','gpus':'GPUInventoryResponse','task':'TaskResponse','connection':'ConnectionResponse',
                                 'nodes':'NodeListResponse','node':'NodeResponse','timeline':'TimelineResponse','performance':'PerformanceResponse'}.get(view.__name__)
                if response_schema:
                    op['responses']['200']['content']['application/json']['schema']={'$ref':'#/components/schemas/'+response_schema}
            if view.api_idempotent and method in {'POST', 'PATCH', 'DELETE'} and method not in view.api_public:
                op['parameters'].append({'name': 'Idempotency-Key', 'in': 'header', 'description': 'For endpoints supporting replay; reuse the same key and body after an uncertain result.', 'schema': S})
            use_schema = schema if method in {'POST', 'PATCH'} else 'GroupDelete' if view.__name__ == 'group' and method == 'DELETE' else None
            if use_schema:
                op['requestBody'] = {'required': use_schema not in {'EmptyRequest', 'GroupDelete', 'TokenIssueRequest'},
                                     'content': {'application/json': {'schema': {'$ref': '#/components/schemas/'+use_schema}}}}
            if method == 'POST' and view.__name__ in {'tasks', 'keys', 'builds', 'members', 'groups', 'token', 'tokens', 'nodes'}:
                op['responses']['201'] = {'description': 'Created'}
            if method == 'POST' and view.__name__ in {'stop', 'save'} or method == 'DELETE' and view.__name__ in {'image', 'volume'}:
                op['responses']['202'] = {'description': 'Asynchronous operation accepted'}
            if view.__name__ in {'download', 'skill_download'}:
                op['responses']['200'] = {'description': 'File download', 'content': {'application/octet-stream': {'schema': {'type': 'string', 'format': 'binary'}}}}
            if view.__name__ == 'schema':
                op['responses']['200'] = {'description': 'This OpenAPI document'}
            operations[method.lower()] = op
        paths[path] = operations
    return {'openapi': '3.0.3', 'info': {'title': '计算资源平台 API', 'version': '1.0.0',
        'description': 'Portal operations use the current token account and user-group quotas. GET /me exposes current permissions. Ownership applies to private resources; staff management does not expose another account\'s credentials or personal build/save history. Dates use ISO 8601; timezone is Asia/Shanghai.'},
        'servers': [{'url': request.build_absolute_uri('/api/v1')}], 'paths': paths,
        'components': {'securitySchemes': {'bearerAuth': {'type': 'http', 'scheme': 'bearer'}}, 'schemas': SCHEMAS}}
