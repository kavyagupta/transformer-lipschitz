from math import ceil
import numpy as np
import torch
import torch.nn as nn 

from advertorch.attacks import L2PGDAttack
from advertorch.context import ctx_noparamgrad_and_eval


# TODO: use args not hard code 

def evaluate_pgd(loader, model, epsilon, niter, alpha):
    model.eval()
    accs = []

    adversary = L2PGDAttack(
        model, loss_fn=nn.CrossEntropyLoss(reduction="sum"), eps=epsilon, 
        nb_iter=niter, eps_iter=alpha, rand_init=True, clip_min=0.0, clip_max=1.0, targeted=False)

    for i, (X,y) in enumerate(loader):
        X,y = X.cuda(), y.cuda()
        with ctx_noparamgrad_and_eval(model):
            X_pgd = adversary.perturb(X, y)
            
        out = model(X_pgd)
        acc = (out.data.max(1)[1] == y).float().sum() / X.size(0)
        accs.append(acc.data.cpu().item())
    print(f'PGD Accuracy {np.mean(accs) * 100:.2f}')

    return np.mean(accs) * 100


def vra_sparse(y_true, y_pred):
    labels = y_true[:,0]

    return torch.sum(labels.eq(torch.argmax(y_pred, axis=1)).float())

def vra_cat(y_true, y_pred):
    labels = torch.argmax(y_true, axis=1)[:,None]

    return vra_sparse(labels, y_pred)

def vra(y_true, y_pred):
    if y_true.shape[1] == 1: 
        return vra_sparse(y_true, y_pred)
    else: 
        return vra_cat(y_true, y_pred)