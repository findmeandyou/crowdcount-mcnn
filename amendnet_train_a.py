import os
import torch
import numpy as np
import sys

sys.path.append('./src/')

from src.AmendNet import MCNN_BackBone, MCNNNet, AmendNet
from src import network
from src.data_loader import ImageDataLoader
from src.timer import Timer
from src import utils
from src.evaluate_model import evaluate_model

try:
    from termcolor import cprint
except ImportError:
    cprint = None

try:
    from pycrayon import CrayonClient
except ImportError:
    CrayonClient = None


def log_print(text, color=None, on_color=None, attrs=None):
    if cprint is not None:
        cprint(text, color=color, on_color=on_color, attrs=attrs)
    else:
        print(text)



method = 'amendnet_saved_models'
dataset_name = 'shtechA'
output_dir = './amendnet_saved_models/'

train_path = './data/formatted_trainval/AmendNet_shanghaitech_part_A_patches_9/train'
train_gt_path = './data/formatted_trainval/AmendNet_shanghaitech_part_A_patches_9/train_den'
val_path = './data/formatted_trainval/AmendNet_shanghaitech_part_A_patches_9/val'
val_gt_path = './data/formatted_trainval/AmendNet_shanghaitech_part_A_patches_9/val_den'



model_path = './final_models/mcnn_shtechA_490.h5'

#training configuration
start_step = 0
end_step = 2000
lr = 0.00001
momentum = 0.9
disp_interval = 500
log_interval = 250


#Tensorboard  config
use_tensorboard = False
save_exp_name = method + '_' + dataset_name + '_' + 'v1'
remove_all_log = False   # remove all historical experiments in TensorBoard
exp_name = None # the previous experiment name in TensorBoard

# ------------
rand_seed = 64678  
if rand_seed is not None:
    np.random.seed(rand_seed)
    torch.manual_seed(rand_seed)
    torch.cuda.manual_seed(rand_seed)


# load mcnn_net and amend_net
mcnn_backbone = MCNN_BackBone()

mcnn_net = MCNNNet(mcnn_backbone=mcnn_backbone)
network.weights_normal_init(mcnn_net, dev=0.01)
mcnn_net.cuda()
mcnn_net.train()
mcnn_net_optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, mcnn_net.parameters()), lr=lr)

amend_net = AmendNet(mcnn_backbone=mcnn_backbone)
network.weights_normal_init(amend_net, dev=0.01)
amend_net.cuda()
amend_net.train()
amend_net_optimizer = torch.optim.Adam(filter(lambda p: p.requires_grad, amend_net.parameters()), lr=lr)

print('Loading the mcnn_backbone...')
network.load_net(model_path, mcnn_backbone, prefix='DME.')
print('Done')

if not os.path.exists(output_dir):
    os.mkdir(output_dir)

# tensorboad
use_tensorboard = use_tensorboard and CrayonClient is not None
if use_tensorboard:
    cc = CrayonClient(hostname='127.0.0.1')
    if remove_all_log:
        cc.remove_all_experiments()
    if exp_name is None:    
        exp_name = save_exp_name 
        exp = cc.create_experiment(exp_name)
    else:
        exp = cc.open_experiment(exp_name)

# training
mcnn_net_train_loss = 0
amend_net_train_loss = 0
step_cnt = 0
re_cnt = False
t = Timer()
t.tic()

data_loader = ImageDataLoader(train_path, train_gt_path, shuffle=True, gt_downsample=True, pre_load=True)
data_loader_val = ImageDataLoader(val_path, val_gt_path, shuffle=False, gt_downsample=True, pre_load=True)
best_mae = sys.maxsize

for epoch in range(start_step, end_step+1):    
    step = -1
    train_loss = 0
    for blob in list(data_loader):
        step = step + 1        
        im_data = blob['data']
        gt_data = blob['gt_density']
        step_cnt += 1
        
        for net in [mcnn_net, amend_net]:
            density_map = net(im_data, gt_data)
            loss = net.loss
            if net is mcnn_net:
                mcnn_net_train_loss += loss.data[0]
                mcnn_net_optimizer.zero_grad()
                loss.backward()
                mcnn_net_optimizer.step()
            elif net is amend_net:
                amend_net_train_loss += loss.data[0]
                amend_net_optimizer.zero_grad()
                loss.backward()
                amend_net_optimizer.step()
            else:
                raise("Net is Neither mcnn_net nor amend_net!")
        
            if step % disp_interval == 0:            
                duration = t.toc(average=False)
                fps = step_cnt / duration
                gt_count = np.sum(gt_data)    
                density_map = density_map.data.cpu().numpy()
                et_count = np.sum(density_map)
                if net is mcnn_net:
                    utils.save_results(im_data,gt_data,density_map, output_dir, fname='mcnnresults.png')
                elif net is amend_net:
                    utils.save_results(im_data,gt_data,density_map, output_dir, fname='amendresults.png')
                net_text = 'mcnn  ' if net is mcnn_net else 'amend '
                log_text = (net_text+'epoch: %4d, step %4d, Time: %.4fs, gt_cnt: %4.1f, et_cnt: %4.1f') \
                                   % (epoch, step, duration, gt_count,et_count)
                log_print(log_text, color='green', attrs=['bold'])
                re_cnt = True    
        
            
        if re_cnt:                                
            t.tic()
            re_cnt = False

    if (epoch % 2 == 0):
        save_name = os.path.join(output_dir, '{}_{}_{}.h5'.format(method,dataset_name,epoch))
        network.save_net(save_name, net)     
        #calculate error on the validation dataset 
        mae,mse = evaluate_model(save_name, data_loader_val, netname='AmendNet')
        if mae < best_mae:
            best_mae = mae
            best_mse = mse
            best_model = '{}_{}_{}.h5'.format(method,dataset_name,epoch)
        log_text = 'EPOCH: %d, MAE: %.1f, MSE: %0.1f' % (epoch,mae,mse)
        log_print(log_text, color='green', attrs=['bold'])
        log_text = 'BEST MAE: %0.1f, BEST MSE: %0.1f, BEST MODEL: %s' % (best_mae,best_mse, best_model)
        log_print(log_text, color='green', attrs=['bold'])
        if use_tensorboard:
            exp.add_scalar_value('MAE', mae, step=epoch)
            exp.add_scalar_value('MSE', mse, step=epoch)
            exp.add_scalar_value('mcnn_net_train_loss', mcnn_net_train_loss/data_loader.get_num_samples(), step=epoch)
            exp.add_scalar_value('amend_net_train_loss', amend_net_train_loss/data_loader.get_num_samples(), step=epoch)
    

