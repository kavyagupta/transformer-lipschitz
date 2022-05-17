import os 
import random
import argparse 
import csv
import io
 
import numpy as np
from PIL import Image
import timm
import torch 
import torch.nn as nn 
import torch.optim as optim 
from torchvision import transforms
import webdataset as wds 


class Byte2Image(object):
    def __call__(self, sample):
        img = Image.open(io.BytesIO(sample)).convert('RGB')
        return img 

def identity(x):
    return x

def train(args, model, device, train_loader,
          optimizer, epoch, criterion, finetune=False):
    model.train()
    train_loss = 0
    correct = 0
    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)
        loss = criterion(output, target)
        loss.backward()
        train_loss += loss.item()
        pred = output.argmax(dim=1, keepdim=True)  # get the index of the max log_probability
        correct += pred.eq(target.view_as(pred)).sum().item()
        optimizer.step()

        torch.cuda.empty_cache()

    train_loss /= 100000
    train_samples = 100000

    print(f"Epoch: {epoch}, Train set: Average loss: {train_loss:.4f}, " +
          f"Accuracy: {correct}/{train_samples} " +
          f"({100.*correct/train_samples:.0f}%), " +
          f"Error: {(train_samples-correct)/train_samples * 100:.2f}%")


def test(args, model, device, test_loader, criterion):
    model.eval()
    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += criterion(output, target).item()  # sum up batch loss
            pred = output.argmax(dim=1, keepdim=True)  # get the index of the max log_probability
            correct += pred.eq(target.view_as(pred)).sum().item()
            torch.cuda.empty_cache()

    test_loss /= 10000
    test_samples = 10000

    print(f"Test set: Average loss: {test_loss:.4f}, " +
          f"Accuracy: {correct}/{test_samples} " +
          f"({100.*correct/test_samples:.0f}%), " +
          f"Error: {(test_samples-correct)/test_samples * 100:.2f}%\n")
    return 100.*correct/test_samples, test_loss


def main():
    # Training settings
    parser = argparse.ArgumentParser(description='PyTorch CIFAR100 ViT')
    parser.add_argument('--task', type=str, default='train',
                        help='train/retrain/extract/test')

    # parser.add_argument('--layers', type=int, default=1)
    # parser.add_argument('--relax', action='store_true')
    # parser.add_argument('--lmbda', type=float, default=1.)
    # parser.add_argument('--warmup', type=int, default=0)
    # parser.add_argument('--attention_type', type=str, default='L2',
    #                     help='L2/DP')

    parser.add_argument('--batch_size', type=int, default=64, metavar='N',
                        help='input batch size for training (default: 64)')
    parser.add_argument('--epochs', type=int, default=14, metavar='N',
                        help='number of epochs to train (default: 10)')
    parser.add_argument('--lr', type=float, default=1e-3, metavar='LR',
                        help='learning rate (default: 1.0)')
    parser.add_argument('--opt', type=str, default='adam',
                        help='adam/sgd')
    parser.add_argument('--num_workers', type=int, default=4,
                        help='Number of cores to use')
    parser.add_argument('--cos', action='store_false', 
                        help='Train with cosine annealing scheduling')

    parser.add_argument('--gpu', type=int, default=0,
                        help='gpu to use')
    parser.add_argument('--seed', type=int, default=2, metavar='S',
                        help='random seed (default: 1)')

    parser.add_argument('--data_path', type=str, required=True,
                        help='data path of CIFAR100')
    parser.add_argument('--weight_path', type=str, required=True,
                        help='weight path of CIFAR100')

    args = parser.parse_args()

    random.seed()
    torch.manual_seed(args.seed)
    device = torch.device(args.gpu)

    print('==> Preparing data..')
    transform_train = transforms.Compose([
        Byte2Image(),
        transforms.Resize(224),
        transforms.RandomHorizontalFlip(0.5),
        transforms.ToTensor(),
        transforms.Normalize([0.4802, 0.4481, 0.3975], [0.2302, 0.2265, 0.2262]),
    ])

    transform_test = transforms.Compose([
        Byte2Image(),
        transforms.Resize(224),
        transforms.ToTensor(),
        transforms.Normalize([0.4802, 0.4481, 0.3975], [0.2302, 0.2265, 0.2262]),
    ])

    train_dataset = (
        wds.WebDataset(os.path.join(args.data_path, 'TinyImageNet/train/train-{00..09}.tar'))
        .shuffle(1000)
        .decode("torchrgb")
        .to_tuple("jpeg.path y.cls")
        .map_tuple(transform_train, identity)
        .batched(args.batch_size, partial=False)
    )
    train_loader = wds.WebLoader(
        train_dataset, batch_size=None, shuffle=False, num_workers=args.num_workers,
    )
    test_dataset = (
        wds.WebDataset(os.path.join(args.data_path, 'TinyImageNet/val/val-{0..0}.tar'))
        .shuffle(1000)
        .decode("torchrgb")
        .to_tuple("jpeg.path y.cls")
        .map_tuple(transform_test, identity)
        .batched(args.batch_size, partial=False)
    )
    test_loader = wds.WebLoader(
        test_dataset, batch_size=None, shuffle=False, num_workers=args.num_workers,
    )

    model = timm.create_model('vit_tiny_patch16_224', pretrained=True)
    model.head = nn.Linear(192, 200)
    model = model.to(device)
    criterion = nn.CrossEntropyLoss()

    if args.opt == 'adam': 
        optimizer = optim.Adam(model.parameters(), lr=args.lr,
                               betas=(0.9, 0.999), weight_decay=5e-5)
    elif args.opt == 'sgd': 
        optimizer = optim.SGD(model.parameters(), lr=args.lr, 
                        momentum=0.9,
                        weight_decay=0.0) 
    # use cosine or reduce LR on Plateau scheduling
    if not args.cos:
        scheduler = optim.lr_scheduler.ReduceLROnPlateau(optimizer, 'min', 
                                                        patience=3, verbose=True, 
                                                        min_lr=1e-3*1e-5, factor=0.1)
    else:
        scheduler = optim.lr_scheduler.CosineAnnealingLR(optimizer, T_max=args.epochs, 
                                                         eta_min=1e-5)

    if args.task == 'train':
        weight_path = os.path.join(args.weight_path, f"vit_tmn_from_pretrained_tiny_patch16_224_seed-{args.seed}_att-DP.pt")

        fout = open(weight_path.replace('.pt', '.csv').replace('weights', 'logs'), 'w')
        w = csv.writer(fout)

        if not os.path.exists(args.weight_path):
            os.mkdir(args.weight_path)

        best_acc = -1
        for epoch in range(1, args.epochs + 1):
            train(args, model, device, train_loader,
                  optimizer, epoch, criterion, False)
            acc, loss = test(args, model, device, test_loader, criterion)
            w.writerow([epoch, acc, loss, 'NaN'])
        
            if args.cos:
                scheduler.step(epoch-1)
            else:
                scheduler.step(loss)
        
            if acc > best_acc:
                best_acc = acc
                torch.save(model.state_dict(), weight_path)
        
        fout.close() 

    if args.task == 'test':
        weight = torch.load(args.weight_path, map_location=device)
        model.load_state_dict(weight)
        test(args, model, device, test_loader, criterion)

if __name__ == '__main__':
    main()