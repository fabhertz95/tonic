from __future__ import print_function
import argparse
import os
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
import torch.optim as optim
from torchvision import datasets, transforms

from spike_data_augmentation.datasets import NMNIST
from spike_data_augmentation import transforms as T

from IPython.core.debugger import set_trace
from tensorboardX import SummaryWriter
from tqdm import tqdm


class Net(nn.Module):
    def __init__(self, n_time_bins=10):
        super(Net, self).__init__()
        self.conv1 = nn.Conv2d(n_time_bins, 20, 7, 1)
        self.conv2 = nn.Conv2d(20, 50, 7, 1)
        self.fc1 = nn.Linear(4 * 4 * 50, 500)
        self.fc2 = nn.Linear(500, 10)

    def forward(self, x):
        x = F.relu(self.conv1(x))
        x = F.max_pool2d(x, 2, 2)
        x = F.relu(self.conv2(x))
        x = F.max_pool2d(x, 2, 2)
        x = x.view(-1, 4 * 4 * 50)
        x = F.relu(self.fc1(x))
        x = self.fc2(x)
        return F.log_softmax(x, dim=1)


log_writer = None
global_step = 0


def log(scalars={}, mode="train"):
    global global_step, log_writer
    for k, v in scalars.items():
        log_writer.add_scalar("%s/%s" % (mode, k), v, global_step)


def train(args, model, device, train_loader, optimizer, epoch):
    model.train()
    for batch_idx, (data, target) in enumerate(tqdm(train_loader)):
        data, target = data.to(device), target.to(device)
        optimizer.zero_grad()
        output = model(data)
        loss = F.nll_loss(output, target)
        loss.backward()
        optimizer.step()
        if batch_idx % args.log_interval == 0:
            log({"loss": loss.item()}, "train")

            global global_step
            global_step += 1


def test(args, model, device, test_loader):
    model.eval()
    test_loss = 0
    correct = 0
    with torch.no_grad():
        for data, target in test_loader:
            data, target = data.to(device), target.to(device)
            output = model(data)
            test_loss += F.nll_loss(
                output, target, reduction="sum"
            ).item()  # sum up batch loss
            pred = output.argmax(
                dim=1, keepdim=True
            )  # get the index of the max log-probability
            correct += pred.eq(target.view_as(pred)).sum().item()

    test_loss /= len(test_loader.dataset)

    log({"loss": test_loss, "accuracy": correct / len(test_loader.dataset)}, "test")


def data_aug_args(parser):
    parser.add_argument("--drop-probability", type=float, help="")
    parser.add_argument("--flip-lr-probability", type=float, help="")
    parser.add_argument("--flip-ud-probability", type=float, help="")
    parser.add_argument("--refractory-period", type=float, help="")
    parser.add_argument("--time-jitter", type=float, help="")


def create_data_aug(args, train=True):
    augmentations = []

    if args.drop_probability is not None and train:
        if args.drop_probability > 1e-4:
            augmentations.append(T.DropEvent(args.drop_probability))
            print("- DropEvent : %f" % args.drop_probability)

    if args.flip_lr_probability is not None and train:
        if args.flip_lr_probability > 1e-4:
            augmentations.append(T.FlipLR(args.flip_lr_probability))
            print("- FlipLR : %f" % args.flip_lr_probability)

    if args.flip_ud_probability is not None and train:
        if args.flip_lr_probability > 1e-4:
            augmentations.append(T.FlipUD(args.flip_ud_probability))
            print("- FlipUD : %f" % args.flip_ud_probability)

    if args.refractory_period is not None and train:
        if args.refractory_period > 1e-4:
            augmentations.append(T.RefractoryPeriod(args.refractory_period))
            print("- RefractoryPeriod : %f" % args.refractory_period)

    if args.time_jitter is not None and train:
        if args.time_jitter > 1e-4:
            arguments.append(T.TimeJitter(args.time_jitter))
            print("- TimeJitter : %f" % args.time_jitter)

    augmentations.append(T.Volume(discrete_xy=True))
    print("- Volume")
    augmentations.append(T.NumpyAsType(np.float32))
    print("- NumpyAsType")

    return T.Compose(augmentations)


def create_data_aug_name(args):
    name = ""

    if args.drop_probability is not None:
        name += "_dp_%0.2f" % args.drop_probability

    if args.flip_lr_probability is not None:
        name += "_flr_%0.2f" % args.flip_lr_probability

    if args.flip_ud_probability is not None:
        name += "_fud_%0.2f" % args.flip_ud_probability

    if args.refractory_period is not None:
        name += "_rp_%0.2f" % args.refractory_period

    if args.time_jitter is not None:
        name += "_tj_%0.2f" % args.time_jitter

    return name


def main():
    # Training settings
    parser = argparse.ArgumentParser(description="PyTorch MNIST Example")
    data_aug_args(parser)
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        metavar="N",
        help="input batch size for training (default: 64)",
    )
    parser.add_argument(
        "--test-batch-size",
        type=int,
        default=1000,
        metavar="N",
        help="input batch size for testing (default: 1000)",
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=10,
        metavar="N",
        help="number of epochs to train (default: 10)",
    )
    parser.add_argument(
        "--lr",
        type=float,
        default=2e-3,
        metavar="LR",
        help="learning rate (default: 2e-3)",
    )
    parser.add_argument(
        "--momentum",
        type=float,
        default=0.5,
        metavar="M",
        help="SGD momentum (default: 0.5)",
    )
    parser.add_argument(
        "--no-cuda", action="store_true", default=False, help="disables CUDA training"
    )
    parser.add_argument(
        "--seed", type=int, default=1, metavar="S", help="random seed (default: 1)"
    )
    parser.add_argument(
        "--log-interval",
        type=int,
        default=10,
        metavar="N",
        help="how many batches to wait before logging training status",
    )

    parser.add_argument(
        "--downloaded", action="store_true", help="Throw if already downloaded"
    )

    parser.add_argument("--logdir", default="../logs", help="Location to save logs to")
    parser.add_argument("--name", required=True, help="Name of experiment")

    args = parser.parse_args()
    use_cuda = not args.no_cuda and torch.cuda.is_available()

    torch.manual_seed(args.seed)

    device = torch.device("cuda" if use_cuda else "cpu")

    kwargs = {"num_workers": 0, "pin_memory": True} if use_cuda else {}
    train_loader = torch.utils.data.DataLoader(
        NMNIST(
            "../data",
            train=True,
            download=not args.downloaded,
            transform=create_data_aug(args, train=True),
            num_events=1000,
        ),
        batch_size=args.batch_size,
        shuffle=True,
        **kwargs
    )
    test_loader = torch.utils.data.DataLoader(
        NMNIST(
            "../data",
            train=False,
            download=not args.downloaded,
            transform=create_data_aug(args, train=False),
            num_events=1000,
        ),
        batch_size=args.test_batch_size,
        shuffle=True,
        **kwargs
    )

    model = Net().to(device)
    optimizer = optim.SGD(model.parameters(), lr=args.lr, momentum=args.momentum)

    global log_writer
    log_writer = SummaryWriter(
        os.path.join(args.logdir, args.name + create_data_aug_name(args))
    )

    for epoch in range(1, args.epochs + 1):
        train(args, model, device, train_loader, optimizer, epoch)
        test(args, model, device, test_loader)


if __name__ == "__main__":
    main()
