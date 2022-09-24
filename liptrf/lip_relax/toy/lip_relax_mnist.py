import os 
import argparse 
import pickle as pkl 
import numpy as np
import tqdm

import torch 
import torch.nn as nn 
import torch.nn.functional as F 
import torch.optim as optim 
from torchvision import datasets, transforms


from liptrf.models.layers.linear import LinearX
from liptrf.models.layers.conv import Conv2dX
from liptrf.models.linear_toy import Net
from liptrf.models.moderate import MNIST_4C3F_ReLUx

from liptrf.utils.evaluate import evaluate_pgd
from liptrf.utils.local_bound import evaluate


def train(args, model, device, train_loader,
          optimizer, epoch, criterion, finetune=False):
    model.train()
    train_loss = 0
    correct = 0
    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model.forward_lip(data)
        loss = criterion(output, target)
        loss.backward()
        train_loss += loss.item()
        pred = output.argmax(dim=1, keepdim=True)  # get the index of the max log_probability
        correct += pred.eq(target.view_as(pred)).sum().item()

        if finetune:
            for name, p in model.named_parameters():
                # print (name)
                if torch.sum(abs(p.data) > 0).item() > 0:
                    p.grad.data *= (abs(p.data) > 0).float() 

        optimizer.step()

    train_loss /= len(train_loader.dataset)
    train_samples = len(train_loader.dataset)

    print(f"Epoch: {epoch}, Train set: Average loss: {train_loss:.4f}, " +
          f"Accuracy: {correct}/{train_samples} " +
          f"({100.*correct/train_samples:.0f}%), " +
          f"Error: {(train_samples-correct)/train_samples * 100:.2f}%")

def test(args, model, device, test_loader, criterion):
    # model.eval()
    test_loss = 0
    correct = 0
    
    lip = model.lipschitz()
    verified = 0

    # with torch.no_grad():
    for data, target in test_loader:
        data, target = data.to(device), target.to(device)
        output = model.forward_lip(data)
        
        test_loss += criterion(output, target).item()  # sum up batch loss
        pred = output.argmax(dim=1, keepdim=True)  # get the index of the max log_probability
        correct += pred.eq(target.view_as(pred)).sum().item()

        # print (output.max())
        one_hot = F.one_hot(target, num_classes=output.shape[-1])
        worst_logit = output + 2**0.5 * 1.58 * lip * (1 - one_hot)
        worst_pred = worst_logit.argmax(dim=1, keepdim=True)
        verified += worst_pred.eq(target.view_as(worst_pred)).sum().item()

        torch.cuda.empty_cache()

    test_samples = len(test_loader.dataset)

    test_loss /= len(test_loader.dataset)
    test_samples = len(test_loader.dataset)
    
    print(f"Test set: Average loss: {test_loss:.4f}, " +
          f"Accuracy: {correct}/{test_samples} " + 
          f"({100.*correct/test_samples:.0f}%), " +
          f"Verified: {100.*verified/test_samples:.0f}%, " +
          f"Error: {(test_samples-correct)/test_samples * 100:.2f}% " +
          f"Lipschitz {model.lipschitz():4f}")
    
    return 100.*verified/test_samples

def process_layers(layers, model, train_loader, test_loader, 
                    criterion, optimizer, args, device):

    best_verified = -1
    for lipr_epoch in range(args.lipr_epochs):
        for layer in layers:
            layer.weight_t = layer.weight.clone().detach()
            if isinstance(layer, Conv2dX):
                layer.weight_t = layer.weight_t.view(layer.weight_t.size(0), -1)
            layer.prox()

            for proj_epoch in tqdm.tqdm(range(args.proj_epochs)):
                layer.proj_weight_old = layer.proj_weight.clone().detach()
                
                for batch_idx, (data, target) in enumerate(train_loader):
                    _ = model(data.to(device))
                    layer.proj()
                    break
                    
                # if torch.linalg.norm(layer.proj_weight - layer.proj_weight_old) < args.proj_prec * torch.linalg.norm(layer.proj_weight):
                #     break 

            layer.update()

            # if torch.linalg.norm(layer.weight_t - layer.weight_old) < args.lipr_prec * torch.norm(layer.weight_t):
            #     break
        
            params = layer.prox_weight.reshape(layer.weight.shape)
            # params[torch.abs(params) < layer.lc_alpha * layer.lipschitz()] = 0
            layer.weight = nn.Parameter(params)
            print (layer.lipschitz())

        test(args, model, device, test_loader, criterion)
        print_nonzeros(model)

        for epoch in range(1, args.epochs + 1):
            train(args, model, device, train_loader,
                    optimizer, epoch, criterion, True)
            # test(args, model, device, test_loader, criterion)
            # print_nonzeros(model)

        acc = test(args, model, device, test_loader, criterion)
        print_nonzeros(model)
        layer.free()

        if acc > best_verified:
            best_verified = acc
            weight_path = args.weight_path.replace('.pt', f"_lc_alpha-{args.lc_alpha}_eta-{args.eta}_lc_gamma-{args.lc_gamma}_lr-{args.lr}.pt")
            torch.save(model.state_dict(), weight_path)
            

def print_nonzeros(model):
    nonzero = total = 0 
    for name, p in model.named_parameters():
        tensor = p.data.cpu().numpy()
        nz_count = np.count_nonzero(abs(tensor) > 0)
        total_params = np.prod(tensor.shape)
        nonzero += nz_count 
        total += total_params 
        # print (f"{name} | nonzeros = {nz_count}/{total_params}" +
        #         f"{100 * nz_count / total_params} | total_pruned = " +
        #         f"{total_params - nz_count} | shape = {tensor.shape}")

    print (f"alive: {nonzero}, pruned : {total - nonzero},"+
           f"total: {total}, Compression rate: {total/nonzero}" +
           f"({100 * (total-nonzero) / total}% pruned)")


def main():
    # Training settings
    parser = argparse.ArgumentParser(description='PyTorch MNIST Toy')

    parser.add_argument('--power_iter', type=int, default=5)
    parser.add_argument('--lc_gamma', default=0.1, type=float)
    parser.add_argument('--lc_alpha', default=0.01, type=float)
    parser.add_argument('--eta', default=1e-2, type=float)
    parser.add_argument('--lr', default=1.2, type=float)
    parser.add_argument('--lipr_epochs', default=2, type=int)
    parser.add_argument('--proj_epochs', default=100, type=int)
    parser.add_argument('--lipr_prec', default=1e-4, type=float)
    parser.add_argument('--proj_prec', default=1e-7, type=float)
    parser.add_argument('--epochs', default=10, type=int)
    
    parser.add_argument('--data', default='mnist', type=str)
    parser.add_argument('--model', default='standrelu', type=str)
    parser.add_argument('--opt', default='adam', type=str)

    parser.add_argument('--train_batch_size', type=int, default=64, metavar='N',
                        help='input batch size for training (default: 64)')
    parser.add_argument('--test_batch_size', type=int, default=64, metavar='N',
                        help='input batch size for training (default: 64)')
    parser.add_argument('--num_workers', type=int, default=4,
                        help='Number of cores to use')

    parser.add_argument('--gpu', type=int, default=0,
                        help='gpu to use')
    parser.add_argument('--seed', type=int, default=2, metavar='S',
                        help='random seed (default: 1)')

    parser.add_argument('--data_path', type=str, required=True,
                        help='data path of MNIST')
    parser.add_argument('--weight_path', type=str, required=True,
                        help='weight path of trained network')

    args = parser.parse_args()

    torch.manual_seed(args.seed)
    device = torch.device(args.gpu)

    if args.model == 'linear':
        transform=transforms.Compose([
        transforms.ToTensor(),
        transforms.Normalize((0.1307,), (0.3081,)),
        transforms.Lambda(lambda x: torch.flatten(x))
        ])
    else:
        transform=transforms.Compose([
            transforms.ToTensor(),
            transforms.Normalize((0.1307,), (0.3081,))
            ])
    dataset1 = datasets.MNIST(args.data_path, train=True, download=True,
                       transform=transform)
    dataset2 = datasets.MNIST(args.data_path, train=False,
                       transform=transform)
    train_loader = torch.utils.data.DataLoader(dataset1, batch_size=args.train_batch_size, 
                                                num_workers=args.num_workers, shuffle=True)
    test_loader = torch.utils.data.DataLoader(dataset2, batch_size=args.test_batch_size, 
                                                num_workers=args.num_workers, shuffle=False)

    if args.model == 'linear':
        model = Net(power_iter=args.power_iter, lmbda=1, 
                     lc_gamma=args.lc_gamma, lc_alpha=args.lc_alpha, 
                     lr=args.lr, eta=args.eta).cuda()
    elif args.model == '4c3f_relux':
        model = MNIST_4C3F_ReLUx(power_iter=args.power_iter, lmbda=1, 
                    lc_gamma=args.lc_gamma, lc_alpha=args.lc_alpha, 
                    lr=args.lr, eta=args.eta).cuda()
    weight = torch.load(args.weight_path)
    model.load_state_dict(weight, strict=False)
    model = model.to(device)
    model.eval()

    criterion = nn.CrossEntropyLoss()
    if args.opt == 'adam': 
        optimizer = optim.Adam(model.parameters(), lr=1e-3)
    elif args.opt == 'sgd': 
        optimizer = optim.SGD(model.parameters(), lr=0.1, 
                        momentum=0.9,
                        weight_decay=0.0) 
    scheduler = torch.optim.lr_scheduler.MultiStepLR(optimizer,
                                                     milestones=[50, 60,
                                                                 70, 80],
                                                     gamma=0.2)
    
    layers = []
    for layer in model.modules():
        if isinstance(layer, Conv2dX) or isinstance(layer, LinearX):
            layers.append(layer)

    print_nonzeros(model)
    process_layers(layers, model, train_loader, test_loader, 
                    criterion, optimizer, args, device)
    test(args, model, device, test_loader, criterion)
    # evaluate(test_loader, model, 1.58, 10, args, None, u_test=None, save_u=False) 
    evaluate_pgd(test_loader, model, epsilon=1.58, niter=20, alpha=1.58/4)
    print_nonzeros(model)
    

if __name__ == '__main__':
    main()