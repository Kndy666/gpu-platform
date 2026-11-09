#!/usr/bin/env python3
"""Headless viewport checks against an isolated preview database."""
import json,os,secrets,subprocess,sys,tempfile,time,urllib.request
from datetime import timedelta
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]

def main():
    out=Path(sys.argv[1] if len(sys.argv)>1 else '/tmp/gpu-platform-ui');out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as data:
        datasets=Path(data)/'datasets';datasets.mkdir();(datasets/'公共数据').mkdir();(datasets/'说明.txt').write_text('数据集说明')
        os.environ['PLATFORM_DATASET_ROOT']=str(datasets)
        os.environ.update(DJANGO_DEBUG='1',PLATFORM_HTTPS='0',PLATFORM_DATA=data,DJANGO_SETTINGS_MODULE='config.settings',DJANGO_ALLOWED_HOSTS='127.0.0.1,localhost')
        sys.path.insert(0,str(ROOT/'backend'))
        subprocess.run([sys.executable,str(ROOT/'backend/manage.py'),'migrate','--run-syncdb','--noinput'],check=True,stdout=subprocess.DEVNULL)
        import django;django.setup()
        from django.utils import timezone
        from portal.models import ResourceRequest,Workspace,StoragePool,SystemObservation,ImageArtifact,UserGroup,AuditLog,SSHKey,VolumeAllocation
        from django.contrib.auth import get_user_model
        password=secrets.token_urlsafe(24)
        group=UserGroup.objects.create(name='临时用户',is_default=True)
        user=get_user_model().objects.create_superuser('preview',password=password)
        member=get_user_model().objects.create_user('preview-member')
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
        from cryptography.hazmat.primitives.serialization import Encoding,PublicFormat
        SSHKey.objects.create(user=user,name='preview',fingerprint='preview',public_key=Ed25519PrivateKey.generate().public_key().public_bytes(Encoding.OpenSSH,PublicFormat.OpenSSH).decode())
        AuditLog.objects.create(actor=user,action='image_build_failed',target='a'*64,detail={'untranslated_key_'+'x'*160:'v'*300,'context':{'path':'/workspace/'+'project'*35,'retry':False}})
        AuditLog.objects.create(action='acceptance_cleanup',target='platform',detail={'users_removed':5,'requests_removed':26,'volumes_removed':31,'remaining_accounts':1,'remaining_requests':0,'production_shared_images':1})
        image=ImageArtifact.objects.create(name='Ubuntu 22.04',reference='registry.example/environment:base',digest='sha256:'+'a'*64,shared=True,verified=True,size_bytes=90000000)
        now=timezone.now()
        req=ResourceRequest.objects.create(user=member,image=image,purpose='视觉模型推理',mode='batch',command='python train.py --epochs 100',argv=['python','train.py'],gpu=2,gpu_requests=[{'compute_percent':100,'memory_gib':8},{'compute_percent':50,'memory_gib':4}],cpu=8,memory_gib=32,status='running')
        Workspace.objects.create(request=req,object_name='preview-run',ssh_port=30042,started_at=now-timedelta(hours=1),gpu_devices=['GPU-preview-1','GPU-preview-0'],gpu_allocations=[{'slot':0,'uuid':'GPU-preview-1','index':1,'sm_count':114},{'slot':1,'uuid':'GPU-preview-0','index':0,'sm_count':56}])
        planned=ResourceRequest.objects.create(user=user,image=image,purpose='模型调试',mode='interactive',gpu=1,gpu_requests=[{'compute_percent':100,'memory_gib':8}],cpu=4,memory_gib=8,starts_at=now+timedelta(hours=1),ends_at=now+timedelta(hours=3),status='scheduled')
        Workspace.objects.create(request=planned,object_name='preview-plan')
        ResourceRequest.objects.create(user=user,image=image,purpose='图像分割实验',mode='interactive',gpu=0,cpu=2,memory_gib=4,starts_at=now,ends_at=now+timedelta(hours=2))
        SystemObservation.objects.create(name='resources',data={'gpu':{'available':3,'total':4,'used_percent':25},'cpu':{'available':72,'total':92,'used_percent':22},'memory_gib':{'available':187,'total':235,'used_percent':20}})
        SystemObservation.objects.create(name='gpu_inventory',data={'devices':[{'uuid':'GPU-preview-'+str(i),'index':i,'name':'RTX 4090 D','memory_mib':24564,'usable_memory_mib':24052,'sm_count':114,'used_compute_percent':50 if i==0 else 0,'used_sm_count':56 if i==0 else 0,'used_memory_mib':8192 if i==0 else 0,'ready':True} for i in range(4)]})
        pool=StoragePool.objects.create(name='disk1',path='/data1',storage_class='preview',capacity_gib=7000,free_gib=6800,healthy=True,observed_at=now)
        development=ResourceRequest.objects.create(user=user,image=image,purpose='GPU 开发环境',mode='interactive',gpu=1,
            gpu_requests=[{'compute_percent':50,'memory_gib':4}],cpu=2,memory_gib=4,workspace_gib=100,
            starts_at=now-timedelta(hours=1),ends_at=now+timedelta(hours=3),status='running')
        Workspace.objects.create(request=development,object_name='preview-development',ssh_port=30043)
        VolumeAllocation.objects.create(user=user,request=development,kind='work',pool=pool,size_gib=100,pvc_name='work-preview',state='bound')
        server=subprocess.Popen([sys.executable,str(ROOT/'backend/manage.py'),'runserver','127.0.0.1:8765','--noreload'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            for _ in range(80):
                try:urllib.request.urlopen('http://127.0.0.1:8765/accounts/login/',timeout=.5);break
                except Exception:time.sleep(.1)
            from playwright.sync_api import sync_playwright
            submitted=[]
            with sync_playwright() as p:
                browser=p.chromium.launch(headless=True,executable_path=os.environ.get('CHROMIUM_PATH'),args=['--no-sandbox'])
                page=browser.new_page(viewport={'width':1920,'height':1080},device_scale_factor=1)
                page.goto('http://127.0.0.1:8765/accounts/login/')
                page.fill('#id_username','preview');page.fill('#id_password',password)
                page.locator('button[type=submit]').click();page.wait_for_url('http://127.0.0.1:8765/')
                errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
                records=[]
                only=set(filter(None,os.environ.get('UI_PAGES','').split(',')))
                for width,height in [(1920,1080),(1366,900),(768,1024),(390,844)]:
                    page.set_viewport_size({'width':width,'height':height})
                    for name,path in [('dashboard','/'),('task',f'/tasks/{req.pk}/'),('environment','/environments/new/'),('program','/training/new/'),('gpu-environment',f'/tasks/{development.pk}/'),('images','/images/'),('management','/management/'),('containers','/management/containers/'),('members','/management/members/'),('member','/management/members/new/'),('groups','/management/groups/'),('group',f'/management/groups/{group.pk}/'),('group-new','/management/groups/new/'),('group-delete',f'/management/groups/{group.pk}/delete/'),('member-edit',f'/management/members/{user.pk}/'),('datasets','/datasets/'),('manage-datasets','/management/datasets/'),('storage','/management/storage/'),('recycling','/management/images/'),('audits','/management/audits/'),('settings','/management/settings/')]:
                        if only and name not in only:continue
                        page.goto('http://127.0.0.1:8765'+path);page.wait_for_load_state('networkidle')
                        if name in ('environment','program'):
                            assert page.locator('[data-capacity-cpu]').inner_text()=='72'
                            assert page.locator('[data-capacity-gpus] .capacity-gpu').count()==4
                            page.locator('#id_gpu').select_option('2')
                            page.locator('[data-gpu-key=compute_percent]').nth(0).fill('100')
                            page.locator('[data-gpu-key=compute_percent]').nth(1).fill('50')
                            page.locator('[data-gpu-key=memory_gib]').nth(0).fill('8')
                            page.locator('[data-gpu-key=memory_gib]').nth(1).fill('4')
                            assert page.locator('[data-gpu-compute-total]').inner_text()=='150'
                            assert page.locator('[data-gpu-key=memory_gib]').nth(1).is_visible()
                            page.locator('#id_gpu').select_option('1')
                            page.locator('#id_gpu').select_option('2')
                            assert page.locator('[data-gpu-key=compute_percent]').nth(1).input_value()=='50'
                            assert page.locator('[data-gpu-key=memory_gib]').nth(1).input_value()=='4'
                            page.locator('#id_gpu').select_option('0')
                            assert not page.locator('[data-gpu-editor]').is_visible()
                            assert page.locator('#id_gpu_requests').input_value()=='[]'
                            page.locator('#id_gpu').select_option('2')
                            page.locator('[data-gpu-max=compute_percent]').nth(1).click()
                            page.locator('[data-gpu-max=memory_gib]').nth(1).click()
                            assert page.locator('[data-gpu-key=compute_percent]').nth(1).input_value()=='100'
                            assert page.locator('[data-gpu-key=memory_gib]').nth(1).input_value()=='23'
                            assert json.loads(page.locator('#id_gpu_requests').input_value())==[{'compute_percent':100,'memory_gib':8},{'compute_percent':'max','memory_gib':'max'}]
                            for key in ('compute_percent','memory_gib'):page.locator(f'[data-gpu-max={key}]').nth(0).click()
                            page.locator('#id_gpu').select_option('4')
                            assert page.locator('[data-gpu-max-error]').is_visible()
                            for index in (2,3):
                                for key in ('compute_percent','memory_gib'):page.locator(f'[data-gpu-max={key}]').nth(index).click()
                            assert not page.locator('[data-gpu-max-error]').is_visible()
                            for key,value in [('compute_percent','50'),('memory_gib','12')]:
                                assert page.locator(f'[data-gpu-key={key}]').evaluate_all('(inputs)=>inputs.every(input=>input.readOnly)')
                                assert page.locator(f'[data-gpu-key={key}]').evaluate_all('(inputs)=>inputs.map(input=>input.value)')==[value]*4
                            page.locator('[data-gpu-max=compute_percent]').nth(0).click()
                            first=page.locator('[data-gpu-key=compute_percent]').nth(0)
                            assert first.input_value()=='50' and not first.evaluate('(input)=>input.readOnly')
                            first.fill('100')
                            assert page.locator('[data-gpu-key=compute_percent]').evaluate_all('(inputs)=>inputs.map(input=>input.value)')==['100','34','33','33']
                            page.locator('[data-gpu-max=compute_percent]').nth(0).click()
                            assert page.locator('[data-gpu-compute-total]').inner_text()=='200'
                            assert page.locator('[data-gpu-memory-total]').inner_text()=='48'
                            assert page.locator('.gpu-value-control').evaluate_all('''controls=>controls.every(control=>
                                [...control.children].every(child=>child.getBoundingClientRect().right<=control.getBoundingClientRect().right+1))''')
                        if name=='gpu-environment':
                            assert page.get_by_role('button',name='保存镜像',exact=True).is_visible()
                            assert page.get_by_role('link',name='运行程序 ↗',exact=True).is_visible()
                        if name=='program':
                            page.locator('[name=source_kind][value=environment]').check()
                            assert page.locator('#id_workspace_gib').is_visible()
                            assert page.locator('#id_workspace_gib').input_value()=='100'
                            page.locator('#id_workspace_gib').fill('40')
                            page.locator('#id_cpu').fill('4')
                            assert page.locator('#id_workspace_gib').input_value()=='40'
                        if name=='containers':
                            page.fill('[name=q]','preview-member')
                            page.locator('.container-filters button').click()
                            assert page.locator('tbody .task-name').all_inner_texts()==['视觉模型推理']
                            page.get_by_role('link',name='SSH 连接：视觉模型推理',exact=True).click()
                            assert page.locator('#connection h2').inner_text()=='管理员 SSH 连接'
                            assert 'User preview-member' in page.locator('#ssh-config').inner_text()
                            page.goto('http://127.0.0.1:8765'+path)
                        if name=='task':
                            assert page.locator('#connection h2').inner_text()=='管理员 SSH 连接'
                            assert page.locator('[data-copy=ssh-command]').is_visible()
                        if name=='audits':
                            page.locator('.audit-entry').evaluate_all('(entries)=>entries.forEach(entry=>entry.open=true)')
                            assert page.get_by_text('已移除账号',exact=True).is_visible()
                            measurements=page.locator('.audit-detail-item').evaluate_all('''items=>items.map(item=>{
                                const label=item.querySelector('dt'),value=item.querySelector('dd');
                                const a=label.getBoundingClientRect(),b=value.getBoundingClientRect(),box=item.getBoundingClientRect();
                                return {separate:a.right<=b.left+1||a.bottom<=b.top+1,
                                    inside:a.left>=box.left-1&&b.left>=box.left-1&&a.right<=box.right+1&&b.right<=box.right+1,
                                    wraps:label.scrollWidth<=label.clientWidth+1&&value.scrollWidth<=value.clientWidth+1};
                            })''')
                            assert measurements and all(all(item.values()) for item in measurements),(width,measurements)
                        dimensions=page.evaluate('({viewport:innerWidth,body:document.documentElement.scrollWidth,main:document.querySelector("main")?.getBoundingClientRect().width})')
                        assert dimensions['body'] <= width+1,(width,name,dimensions)
                        if width>=1366:assert dimensions['main']>=width*.8,(width,name,dimensions)
                        if name in ('member','member-edit'):
                            form=page.locator('.member-form').bounding_box();content=page.locator('.content').bounding_box()
                            assert form['width']>=content['width']*.85,(width,name,form,content)
                        if name=='dashboard':assert page.locator('.allocation-row').count()>=7
                        if width in (1920,390):page.screenshot(path=str(out/f'{name}-{width}.png'),full_page=True)
                        if name in ('environment','program'):
                            page.fill('#id_purpose',f'Maximum {name} {width}')
                            if name=='program':page.fill('#id_command','true')
                            page.locator('button[type=submit]').click()
                            page.wait_for_url('**/tasks/*/')
                            submitted.append(f'Maximum {name} {width}')
                        records.append({'page':name,'width':width,'overflow':dimensions['body']-width})
                assert not errors,errors
                browser.close()
            for purpose in submitted:
                assert ResourceRequest.objects.get(purpose=purpose).gpu_requests==[{'compute_percent':50,'memory_gib':12}]*4
            print(json.dumps({'viewport_checks':records,'browser_errors':errors},ensure_ascii=False))
        finally:
            server.terminate();server.wait(timeout=10)

if __name__=='__main__':main()
