import os 
import argparse 
import pickle as pkl 
import numpy as np 

import timm
import torch 
import torch.nn as nn 
import torch.nn.functional as F 
import torch.optim as optim 
from torchvision import datasets, transforms

from liptrf.models.vit import L2Attention
from liptrf.models.layers import LinearX
from liptrf.models.timm_vit import VisionTransformer as ViT


attention_io = {}
def get_activation(name):
    def hook(model, input, output):
        attention_io[name] = {"in": input[0].detach(),
                              "out": output.detach()}
    return hook 

def train(args, dp_model, l2_model, student_l2_models, device, train_loader,
          student_l2_optims, epoch, criterion, finetune=False):
    student_l2_models = [student_l2_model.train() for student_l2_model in student_l2_models]
    dp_model.eval()

    student_l2_train_loss = [0] * len(student_l2_models)
    for batch_idx, (data, target) in enumerate(train_loader):
        data, target = data.to(device), target.to(device)
        output = dp_model(data)

        [student_l2_optim.zero_grad() for student_l2_optim in student_l2_optims]
        for i in range(args.layers):
            teacher_l2_data = attention_io[f"a{i}"]["in"]
            teacher_l2_target = attention_io[f"a{i}"]["out"]
            student_l2_output = student_l2_models[i](teacher_l2_data)
            student_l2_loss = criterion(student_l2_output, teacher_l2_target)
            student_l2_loss.backward()
            student_l2_train_loss[i] += student_l2_loss.item()
            student_l2_optims[i].step()

    for i in range(args.layers):
        l2_model.blocks[i].attn = student_l2_models[i]

    student_l2_train_loss = [student_l2_loss /len(train_loader.dataset) for student_l2_loss in student_l2_train_loss]

    print(f"Epoch: {epoch}, Train set: Average losses")
    for i in range(args.layers):
        print(f"Attention {i}: {student_l2_train_loss[i]}")


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

    test_loss /= len(test_loader.dataset)
    test_samples = len(test_loader.dataset)

    print(f"Test set: Average loss: {test_loss:.4f}, " +
          f"Accuracy: {correct}/{test_samples} " +
          f"({100.*correct/test_samples:.0f}%), " +
          f"Error: {(test_samples-correct)/test_samples * 100:.2f}% " +
          f"Lipschitz {model.lipschitz().item():4f} \n")
    return 100.*correct/test_samples

def main():
    # Training settings
    parser = argparse.ArgumentParser(description='PyTorch MNIST ViT')
    parser.add_argument('--task', type=str, default='train',
                        help='train/retrain/extract/test')

    parser.add_argument('--layers', type=int, default=1)
    
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

    parser.add_argument('--gpu', type=int, default=0,
                        help='gpu to use')
    parser.add_argument('--seed', type=int, default=2, metavar='S',
                        help='random seed (default: 1)')

    parser.add_argument('--data_path', type=str, required=True,
                        help='data path of MNIST')
    parser.add_argument('--dp_weight_path', type=str, required=True,
                        help='weight path of ViT trained with DP attention')

    args = parser.parse_args()

    torch.manual_seed(args.seed)
    device = torch.device(args.gpu)

    print('==> Preparing data..')
    transform_train = transforms.Compose([
        transforms.Resize(224),
        transforms.RandomHorizontalFlip(),
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    ])

    transform_test = transforms.Compose([
        transforms.Resize(224),
        transforms.ToTensor(),
        transforms.Normalize((0.4914, 0.4822, 0.4465), (0.2023, 0.1994, 0.2010)),
    ])

    trainset = datasets.CIFAR10(
        root=args.data_path, train=True, download=True, transform=transform_train)
    train_loader = torch.utils.data.DataLoader(
        trainset, batch_size=args.batch_size, shuffle=True, num_workers=args.num_workers)

    testset = datasets.CIFAR10(
        root=args.data_path, train=False, download=True, transform=transform_test)
    test_loader = torch.utils.data.DataLoader(
        testset, batch_size=args.batch_size, shuffle=False, num_workers=args.num_workers)

    l2_model =  ViT(patch_size=16, embed_dim=192, depth=12, num_heads=3, lmbda=1, num_classes=10)
    dp_model = timm.create_model('vit_tiny_patch16_224', pretrained=False, num_classes=10)
    args.layers = 12
    weight = torch.load(args.dp_weight_path)
    dp_model.load_state_dict(weight)
    dp_model.eval()

    l2_model.load_state_dict(weight, strict=False)

    student_l2_criterion = nn.MSELoss()
    student_l2_optims = []
    student_l2_schedulers = []
    student_l2_models = []
    for i in range(args.layers):
        dp_model.blocks[i].attn.register_forward_hook(get_activation(f'a{i}'))
        student_l2_model = L2Attention(dim=192, heads=3).cuda()

        if args.opt == 'adam': 
            student_l2_optimizer = optim.Adam(student_l2_model.parameters(), lr=args.lr)
        elif args.opt == 'sgd': 
            student_l2_optimizer = optim.SGD(student_l2_model.parameters(), lr=args.lr, 
                            momentum=0.9,
                            weight_decay=0.0) 
        student_l2_scheduler = torch.optim.lr_scheduler.MultiStepLR(student_l2_optimizer,
                                                        milestones=[50, 60,
                                                                    70, 80],
                                                        gamma=0.2)
        student_l2_models.append(student_l2_model)
        student_l2_optims.append(student_l2_optimizer)
        student_l2_schedulers.append(student_l2_scheduler)

    criterion = nn.CrossEntropyLoss()

    if args.task == 'train':
        weight_path = os.path.join(args.dp_weight_path.replace('.pt', '_L2-Adapted.pt'))

        if not os.path.exists(args.weight_path):
            os.mkdir(args.weight_path)

        best_acc = -1
        for epoch in range(1, args.epochs + 1):
            train(args, dp_model, l2_model, student_l2_models, device, train_loader,
                  student_l2_optims, epoch, student_l2_criterion, False)
            acc = test(args, l2_model, device, test_loader, criterion)
            
            for student_l2_scheduler in student_l2_schedulers:
                student_l2_scheduler.step()

            if acc > best_acc:
                torch.save(l2_model.state_dict(), weight_path)
            

if __name__ == '__main__':
    main()