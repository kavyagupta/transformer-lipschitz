### basic modules
import numpy as np
import time, pickle, os, sys, json, PIL, tempfile, warnings, importlib, math, copy, shutil

### torch modules
import torch
import torch.nn as nn
import torch.optim as optim
from torchvision import datasets, transforms
from torch.optim.lr_scheduler import StepLR, MultiStepLR
import torch.nn.functional as F

from advertorch.attacks import L2PGDAttack
from advertorch.context import ctx_noparamgrad_and_eval

import argparse

def argparser(data='cifar10', model='c6f2_relux',
              batch_size=256, epochs=800, warmup=20, rampup=400,
              epsilon=36/255, epsilon_train=0.1551, starting_epsilon=0.0, 
              opt='adam', lr=0.001): 

    parser = argparse.ArgumentParser()
    # log settings
    parser.add_argument('--project', default='debug_bcp', help='Name for Wandb project')
    
    # main settings
    parser.add_argument('--rampup', type=int, default=rampup)
    parser.add_argument('--warmup', type=int, default=warmup)
    parser.add_argument('--sniter', type=int, default=1) 
    parser.add_argument('--opt_iter', type=int, default=1) 
    parser.add_argument('--no_save', action='store_true') 
    parser.add_argument('--test_pth', default=None)
    parser.add_argument('--print', action='store_true')

    # optimizer settings
    parser.add_argument('--opt', default='adam')
    parser.add_argument('--momentum', type=float, default=0.9)
    parser.add_argument('--weight_decay', type=float, default=0.0)
    parser.add_argument('--epochs', type=int, default=epochs)
    parser.add_argument("--lr", type=float, default=lr)
    parser.add_argument("--end_lr", type=float, default=5e-6)
    parser.add_argument("--step_size", type=int, default=10)
    parser.add_argument("--gamma", type=float, default=0.5)
    parser.add_argument("--wd_list", nargs='*', type=int, default=None)
    parser.add_argument("--lr_scheduler", default='exp')
    parser.add_argument('--more', type=int, default=25) # more epochs with initial learning rate before exp decay
    
    # test settings during training
    parser.add_argument('--test_sniter', type=int, default=100) 
    parser.add_argument('--test_opt_iter', type=int, default=1000) 

    # pgd settings
    parser.add_argument("--epsilon_pgd", type=float, default=epsilon)
    parser.add_argument("--alpha", type=float, default=epsilon/4)
    parser.add_argument("--niter", type=float, default=100)
    
    # epsilon settings
    parser.add_argument("--epsilon", type=float, default=epsilon)
    parser.add_argument("--epsilon_train", type=float, default=epsilon_train)
    parser.add_argument("--starting_epsilon", type=float, default=starting_epsilon)
    parser.add_argument('--schedule_length', type=int, default=rampup) 
    
    # kappa settings
    parser.add_argument("--kappa", type=float, default=0.0)
    parser.add_argument("--starting_kappa", type=float, default=1.0)
    parser.add_argument('--kappa_schedule_length', type=int, default=rampup) 
    
    # use gloro loss with auxillary logit
    parser.add_argument('--gloro', action='store_true')

    # model arguments
    parser.add_argument('--model', default='large')
    parser.add_argument("--init", type=float, default=1.0)

    # other arguments
    parser.add_argument('--prefix')
    parser.add_argument('--saved_model', default=None)
    parser.add_argument('--data', default=data)
    parser.add_argument('--real_time', action='store_true')
    parser.add_argument('--seed', type=int, default=2019)
    parser.add_argument('--verbose', type=int, default=200)
    parser.add_argument('--cuda_ids', type=int, default=0)
    
    # loader arguments
    parser.add_argument('--batch_size', type=int, default=batch_size)
    parser.add_argument('--test_batch_size', type=int, default=batch_size)
    parser.add_argument('--normalization', action='store_true')
    parser.add_argument('--no_augmentation', action='store_true')
    parser.add_argument('--drop_last', action='store_true')
    parser.add_argument('--no_shuffle', action='store_true')

    parser.add_argument('--data_path', type=str, required=True,
                        help='data path of MNIST')
    parser.add_argument('--weight_path', type=str, required=True,
                        help='weight path of MNIST')
    
    args = parser.parse_args()
    
    args.augmentation = not(args.no_augmentation)
    args.shuffle = not(args.no_shuffle)
    args.save = not(args.no_save)
    
    if args.rampup:
        args.schedule_length = args.rampup
        args.kappa_schedule_length = args.rampup 
    if args.epsilon_train is None:
        args.epsilon_train = args.epsilon 
        
    if args.starting_epsilon is None:
        args.starting_epsilon = args.epsilon
    if args.prefix:
        args.prefix = 'pretrained/'+args.prefix
        if args.model is not None: 
            args.prefix += '_'+args.model
        if args.schedule_length > args.epochs: 
            raise ValueError('Schedule length for epsilon ({}) is greater than '
                             'number of epochs ({})'.format(args.schedule_length, args.epochs))
    else: 
        args.prefix = 'pretrained/temporary'

    if args.cuda_ids is not None: 
        print('Setting CUDA_VISIBLE_DEVICES to {}'.format(args.cuda_ids))
        torch.cuda.set_device(args.cuda_ids)

    return args

def select_model(data, m, init): 
    if data=='mnist':
        if m=='4c3f_relux':
            model = mnist_model_large_relux().cuda()
        elif m=='4c3f_relu':
            model = mnist_model_large_standrelu().cuda()
    elif data=='cifar10':
        if m=='4c3f_relux':
            model = cifar_model_large_relux().cuda()
        elif m=='c6f2_relux':
            model = c6f2_relux(init=init).cuda()
        elif m=='c6f2_clmaxmin':
            model = c6f2_clmaxmin().cuda()
        elif m=='c6f2_relu':
            model = c6f2_standrelu().cuda()
    elif data=='cifar100':
        if m=='8c2f_relux':
            model = cifar100_relux().cuda() 
    elif data=='tinyimagenet':
        if m == '8c2f_relux':
            model = tinyimagenet_relux(init=init).cuda()
    return model

def mnist_model_large_relux(): 
    model = nn.Sequential(
        nn.Conv2d(1, 32, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 32, 28, 28])),
        nn.Conv2d(32, 32, 4, stride=2, padding=1),
        ReLU_x(torch.Size([1, 32, 14, 14])),
        nn.Conv2d(32, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 14, 14])),
        nn.Conv2d(64, 64, 4, stride=2, padding=1),
        ReLU_x(torch.Size([1, 64, 7, 7])),
        Flatten(),
        nn.Linear(64*7*7,512),
        ReLU_x(torch.Size([1, 512])),
        nn.Linear(512,512),
        ReLU_x(torch.Size([1, 512])),
        nn.Linear(512,10)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def mnist_model_large_standrelu(): 
    model = nn.Sequential(
        nn.Conv2d(1, 32, 3, stride=1, padding=1),
        nn.ReLU(),
        nn.Conv2d(32, 32, 4, stride=2, padding=1),
        nn.ReLU(),
        nn.Conv2d(32, 64, 3, stride=1, padding=1),
        nn.ReLU(),
        nn.Conv2d(64, 64, 4, stride=2, padding=1),
        nn.ReLU(),
        Flatten(),
        nn.Linear(64*7*7,512),
        nn.ReLU(),
        nn.Linear(512,512),
        nn.ReLU(),
        nn.Linear(512,10)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def cifar_model_large_relux(): 
    model = nn.Sequential(
        nn.Conv2d(3, 32, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 32, 32, 32])),
        nn.Conv2d(32, 32, 4, stride=2, padding=1),
        ReLU_x(torch.Size([1, 32, 16, 16])),
        nn.Conv2d(32, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 16, 16])),
        nn.Conv2d(64, 64, 4, stride=2, padding=1),
        ReLU_x(torch.Size([1, 64, 8, 8])),
        Flatten(),
        nn.Linear(64*8*8,512),
        ReLU_x(torch.Size([1, 512])),
        nn.Linear(512,512),
        ReLU_x(torch.Size([1, 512])),
        nn.Linear(512,10)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def c6f2_relux(init=2.0): 
    model = nn.Sequential(
        nn.Conv2d(3, 32, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 32, 32, 32]), init),
        nn.Conv2d(32, 32, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 32, 32, 32]), init),
        nn.Conv2d(32, 32, 4, stride=2, padding=1),
        ReLU_x(torch.Size([1, 32, 16, 16]), init),
        nn.Conv2d(32, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 16, 16]), init),
        nn.Conv2d(64, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 16, 16]), init),
        nn.Conv2d(64, 64, 4, stride=2, padding=1),
        ReLU_x(torch.Size([1, 64, 8, 8]), init),
        Flatten(),
        nn.Linear(4096,512),
        ReLU_x(torch.Size([1, 512]), init),
        nn.Linear(512,10)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def c6f2_standrelu(): 
    model = nn.Sequential(
        nn.Conv2d(3, 32, 3, stride=1, padding=1),
        nn.ReLU(),
        nn.Conv2d(32, 32, 3, stride=1, padding=1),
        nn.ReLU(),
        nn.Conv2d(32, 32, 4, stride=2, padding=1),
        nn.ReLU(),
        nn.Conv2d(32, 64, 3, stride=1, padding=1),
        nn.ReLU(),
        nn.Conv2d(64, 64, 3, stride=1, padding=1),
        nn.ReLU(),
        nn.Conv2d(64, 64, 4, stride=2, padding=1),
        nn.ReLU(),
        Flatten(),
        nn.Linear(4096,512),
        nn.ReLU(),
        nn.Linear(512,10)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def c6f2_clmaxmin(maxthres=0.5, minthres=-0.5):
    model = nn.Sequential(
        nn.Conv2d(3, 32, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 16, 32, 32]), 0.1, -0.1),
        nn.Conv2d(32, 32, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 16, 32, 32]), 0.1, -0.15),
        nn.Conv2d(32, 32, 4, stride=2, padding=1),
        ClampGroupSort(torch.Size([1, 16, 16, 16]), 0.15, -0.15),
        nn.Conv2d(32, 64, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 32, 16, 16]), 0.15, -0.15),
        nn.Conv2d(64, 64, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 32, 16, 16]), 0.2, -0.2),
        nn.Conv2d(64, 64, 4, stride=2, padding=1),
        ClampGroupSort(torch.Size([1, 32, 8, 8]), 0.3, -0.3),
        Flatten(),
        nn.Linear(4096,512),
        ClampGroupSort(torch.Size([1, 256]), 1.2, -1.2),
        nn.Linear(512,10)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def cifar100_relux(init=1.0): 
    model = nn.Sequential(
        nn.Conv2d(3, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 32, 32]), init),
        nn.Conv2d(64, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 32, 32]), init),
        nn.Conv2d(64, 64, 4, stride=2),
        ReLU_x(torch.Size([1, 64, 15, 15]), init),
        nn.Conv2d(64, 128, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 128, 15, 15]), init),
        nn.Conv2d(128, 128, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 128, 15, 15]), init),
        nn.Conv2d(128, 128, 4, stride=2),
        ReLU_x(torch.Size([1, 128, 6, 6]), init),
        nn.Conv2d(128, 256, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 256, 6, 6]), init),
        nn.Conv2d(256, 256, 4, stride=2),
        ReLU_x(torch.Size([1, 256, 2, 2]), init),
        Flatten(),
        nn.Linear(1024, 256),
        ReLU_x(torch.Size([1, 256]), init),
        nn.Linear(256, 100)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def tinyimagenet_relux(init=1.0): 
    model = nn.Sequential(
        nn.Conv2d(3, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 64, 64]), init),
        nn.Conv2d(64, 64, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 64, 64, 64]), init),
        nn.Conv2d(64, 64, 4, stride=2),
        ReLU_x(torch.Size([1, 64, 31, 31]), init),
        nn.Conv2d(64, 128, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 128, 31, 31]), init),
        nn.Conv2d(128, 128, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 128, 31, 31]), init),
        nn.Conv2d(128, 128, 4, stride=2),
        ReLU_x(torch.Size([1, 128, 14, 14]), init),
        nn.Conv2d(128, 256, 3, stride=1, padding=1),
        ReLU_x(torch.Size([1, 256, 14, 14]), init),
        nn.Conv2d(256, 256, 4, stride=2),
        ReLU_x(torch.Size([1, 256, 6, 6]), init),
        Flatten(),
        nn.Linear(9216,256),
        ReLU_x(torch.Size([1, 256]), init),
        nn.Linear(256,200)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

def tinyimagenet_clmaxmin(maxthres=0.5, minthres=-0.5): 
    model = nn.Sequential(
        nn.Conv2d(3, 64, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 32, 64, 64]), 0.4*2, -0.4*2),
        nn.Conv2d(64, 64, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 32, 64, 64]), 0.5*2, -0.5*2),
        nn.Conv2d(64, 64, 4, stride=2),
        ClampGroupSort(torch.Size([1, 32, 31, 31]), 0.7*2, -0.7*2),
        nn.Conv2d(64, 128, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 64, 31, 31]), 0.7*2, -0.7*2),
        nn.Conv2d(128, 128, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 64, 31, 31]), 0.7*2, -0.7*2),
        nn.Conv2d(128, 128, 4, stride=2),
        ClampGroupSort(torch.Size([1, 64, 14, 14]), 0.8*2, -0.8*2),
        nn.Conv2d(128, 256, 3, stride=1, padding=1),
        ClampGroupSort(torch.Size([1, 128, 14, 14]), 1.0*2, -1.0*2),
        nn.Conv2d(256, 256, 4, stride=2),
        ClampGroupSort(torch.Size([1, 128, 6, 6]), 1.5*2, -1.5*2),
        Flatten(),
        nn.Linear(9216,256),
        ClampGroupSort(torch.Size([1, 128]), 0.8*2, -0.8*2),
        nn.Linear(256,200)
    )
    for m in model.modules():
        if isinstance(m, nn.Conv2d):
            n = m.kernel_size[0] * m.kernel_size[1] * m.out_channels
            m.weight.data.normal_(0, math.sqrt(2. / n))
            m.bias.data.zero_()
    return model

#### Layers: Flatten / learnable ReLU / Clamped learnable MaxMin / one_hot

class Flatten(nn.Module): ## =nn.Flatten()
    def forward(self, x):
        return x.view(x.size()[0], -1)

class ReLU_x(nn.Module):
    # learnable relu, has a threshold for each input entry
    def __init__ (self, input_size, init=1.0, **kwargs):
        super(ReLU_x, self).__init__(**kwargs)
        self.threshold = nn.Parameter(torch.Tensor(input_size))
        self.threshold.data.fill_(init)

    def forward(self, x):
        return torch.clamp(torch.min(x, self.threshold), min=0.0)

class ClampGroupSort(nn.Module):
    def __init__(self, input_size, maxthres, minthres):
        super().__init__()
        # input size should be the half channel size as the actual size
        self.min = nn.Parameter(torch.Tensor(input_size))
        self.min.data.fill_(minthres)
        
        self.max = nn.Parameter(torch.Tensor(input_size))
        self.max.data.fill_(maxthres)
        
    def forward(self, x):
        a, b = x.split(x.size(1) // 2, 1)
        a, b = torch.min(torch.max(a, b), self.max), torch.max(torch.min(a, b), self.min)
        return torch.cat([a, b], dim=1)

def one_hot(batch, depth=10):
    ones = torch.eye(depth, device=batch.device)
    return ones.index_select(0,batch)


#### clean train
def train(loader, model, opt, epoch, log, verbose):
    batch_time = AverageMeter()
    data_time = AverageMeter()
    losses = AverageMeter()
    errors = AverageMeter()
    
    model.train()

    end = time.time()
    for i, (X,y,idx) in enumerate(loader):
        X,y = X.cuda(), y.cuda()
        data_time.update(time.time() - end)
 
        out = model(X)
        ce = nn.CrossEntropyLoss()(out, y)
        err = (out.max(1)[1] != y).float().sum()  / X.size(0)
        loss = ce
        
        opt.zero_grad()
        loss.backward()
        opt.step()

        # measure accuracy and record loss
        losses.update(ce.item(), X.size(0))
        errors.update(err.item(), X.size(0))
        
        # measure elapsed time
        batch_time.update(time.time()-end)
        end = time.time()

        print(epoch, i, ce.item(), file=log) ########
        if verbose and (i==0 or i==len(loader)-1 or (i+1) % verbose == 0): 
            print('Epoch: [{0}][{1}/{2}]\t'
                  'Time {batch_time.val:.4f} ({batch_time.avg:.4f})\t'
                  'Data {data_time.val:.4f} ({data_time.avg:.4f})\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                  'Error {errors.val:.4f} ({errors.avg:.4f})'.format(
                   epoch, i+1, len(loader), batch_time=batch_time,
                   data_time=data_time, loss=losses, errors=errors))
        log.flush()
    return

#### Evaluation code
def evaluate(loader, model, epoch, log, verbose):
    batch_time = AverageMeter()
    losses = AverageMeter()
    errors = AverageMeter()

    model.eval()

    end = time.time()
    for i, (X,y,idx) in enumerate(loader):
        X,y = X.cuda(), y.cuda()
        out = model(X)
        ce = nn.CrossEntropyLoss()(out, y)
        err = (out.data.max(1)[1] != y).float().sum()  / X.size(0)

        # print to logfile
        print(epoch, i, ce.item(), err.item(), file=log)

        # measure accuracy and record loss
        losses.update(ce.data, X.size(0))
        errors.update(err, X.size(0))

        # measure elapsed time
        batch_time.update(time.time() - end)
        end = time.time()

        if verbose and (i==0 or i==len(loader)-1 or (i+1) % verbose == 0): 
            print('Test: [{0}/{1}]\t'
                  'Time {batch_time.val:.4f} ({batch_time.avg:.4f})\t'
                  'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
                  'Error {error.val:.4f} ({error.avg:.4f})'.format(
                      i+1, len(loader), batch_time=batch_time, loss=losses,
                      error=errors))
        log.flush()

    print(' * Error {error.avg:.4f}'.format(error=errors))
    return errors.avg


def evaluate_pgd(loader, model, args):
    errors = AverageMeter()
    model.eval()

    adversary = L2PGDAttack(
        model, loss_fn=nn.CrossEntropyLoss(reduction="sum"), eps=args.epsilon, 
        nb_iter=args.niter, eps_iter=args.alpha, rand_init=True, clip_min=0.0,
         clip_max=1.0, targeted=False)

    end = time.time()
    for i, (X,y,idx) in enumerate(loader):
        X,y = X.cuda(), y.cuda()
        with ctx_noparamgrad_and_eval(model):
            X_pgd = adversary.perturb(X, y)
            
        out = model(X_pgd)
        err = (out.data.max(1)[1] != y).float().sum() / X.size(0)
        errors.update(err, X.size(0))
    print(' * Error {error.avg:.4f}'.format(error=errors))
    return errors.avg

class AverageMeter(object):
    """Computes and stores the average and current value"""
    def __init__(self):
        self.reset()

    def reset(self):
        self.val = 0
        self.avg = 0
        self.sum = 0
        self.count = 0

    def update(self, val, n=1):
        self.val = val
        self.sum += val * n
        self.count += n
        self.avg = self.sum / self.count    


# train with the same learning rate for the larest eps for some more epochs and then decay
def lr_exp(start_lr, end_lr, epoch, max_epoch, more=25):
    if epoch >= (max_epoch//2 + more):
        scalar = (end_lr/start_lr)**((float(epoch)-(max_epoch//2 +more-1))/(max_epoch//2 - more))
    else:
        scalar = 1.0
    return scalar   