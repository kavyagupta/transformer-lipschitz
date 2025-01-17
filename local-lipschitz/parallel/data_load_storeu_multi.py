### basic modules
import numpy as np
import time, pickle, os, sys, json, PIL, tempfile, warnings, importlib, math, copy, shutil, setproctitle

### torch modules
import torch
import torch.nn as nn
import torch.optim as optim
from torch.autograd import Variable
from torchvision import datasets, transforms
from torch.optim.lr_scheduler import StepLR, MultiStepLR
import torch.nn.functional as F
from torch import autograd

from torch.utils.data import Dataset, DataLoader, TensorDataset
from torch.optim.lr_scheduler import StepLR, MultiStepLR
import torch.utils.data.distributed

# data loader that returns indices of images
class MyDataset(Dataset):
    def __init__(self, trainset):
        self.set = trainset
        
    def __getitem__(self, index):
        data, target = self.set[index]        
        return data, target, index

    def __len__(self):
        return len(self.set)

def data_loaders(data_path, data, batch_size, test_batch_size, augmentation=False, normalization=False, drop_last=False, shuffle=True):

    if not os.path.exists(data_path):
        os.makedirs(data_path)

    if data == 'cifar10': 
        trainset_transforms = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize((0.485, 0.456, 0.406), (0.225, 0.225, 0.225))])
        trainset = datasets.CIFAR10(root=data_path, train=True, download=True, transform=trainset_transforms)
        testset = datasets.CIFAR10(root=data_path, train=False, download=True, transform=transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.485, 0.456, 0.406), (0.225, 0.225, 0.225))]))
    
    if data == 'cifar100':
        trainset_transforms = transforms.Compose([
            transforms.RandomCrop(32, padding=4),
            transforms.RandomHorizontalFlip(),
            transforms.ToTensor(),
            transforms.Normalize((0.5071, 0.4867, 0.4408), (0.2675, 0.2565, 0.2761))])
        trainset = datasets.CIFAR10(root=data_path, train=True, download=True, transform=trainset_transforms)
        testset = datasets.CIFAR10(root=data_path, train=False, download=True, transform=transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.5071, 0.4867, 0.4408), (0.2675, 0.2565, 0.2761))]))
        
    if data == 'mnist':
        trainset = datasets.MNIST(root=data_path, train=True, download=True, transform=transforms.ToTensor())
        testset = datasets.MNIST(root=data_path, train=False, download=True, transform=transforms.ToTensor())
        
    if data == 'mnist':
        trainset = datasets.MNIST(root=data_path, train=True, download=True, transform=transforms.ToTensor())
        testset = datasets.MNIST(root=data_path, train=False, download=True, transform=transforms.ToTensor())
    
    if data == 'tinyimagenet':
        trainset_transforms = transforms.Compose([
                transforms.RandomCrop(64, padding=4),
                transforms.RandomHorizontalFlip(),
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])])
        trainset = datasets.ImageFolder(root=f'{data_path}/tiny-imagenet-200/train', transform=trainset_transforms)
        testset = datasets.ImageFolder(root=f'{data_path}/tiny-imagenet-200/val', transform=transforms.Compose([
                transforms.ToTensor(),
                transforms.Normalize(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5])]))
        
    ##### Distributed Training #####
    train_sampler = None
    val_sampler = None
    train_data = MyDataset(trainset)
    test_data = MyDataset(testset)
    
    train_sampler = torch.utils.data.distributed.DistributedSampler(train_data)   
    val_sampler = torch.utils.data.distributed.DistributedSampler(test_data)
    
    data_loader  = torch.utils.data.DataLoader(dataset=train_data,
                                              batch_size=batch_size,
                                              shuffle=(train_sampler is None), 
                                              drop_last = drop_last,
                                              num_workers=4, pin_memory=True, sampler=train_sampler)
    test_data_loader  = torch.utils.data.DataLoader(dataset=test_data,
                                              batch_size=test_batch_size,
                                              shuffle=False,
                                              num_workers=4, pin_memory=True, sampler=val_sampler)
    
    return data_loader, test_data_loader
