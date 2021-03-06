import torch
import torch.nn as nn

from torch.optim.lr_scheduler import ReduceLROnPlateau
from torch.utils.data import DataLoader
from torchvision import transforms

import data
import utils
import eval
from model_covid import CovidNet, ResNet


def calculateDataLoaderTrain(args_dict):
    # Augmentation
    train_transformation = transforms.Compose([
        transforms.Resize(256),  # rescale the image keeping the original aspect ratio
        transforms.CenterCrop(256),  # we get only the center of that rescaled
        transforms.RandomCrop(224),  # random crop within the center crop (data augmentation)
        transforms.ColorJitter(brightness=(0.9, 1.1)),
        transforms.RandomRotation((-10, 10)),
        transforms.RandomHorizontalFlip(),
        transforms.RandomAffine(0, translate=(0.1, 0.1), shear=10, scale=(0.85, 1.15), fillcolor=0),
        # TransformShow(), # visualize transformed pic
        transforms.ToTensor(),
    ])

    # Dataloaders for training and validation
    # preprocess the given txt files: Train
    datasets_train, _, labels, labels_non, labels_cov = data.preprocessSplit(args_dict.train_txt)

    # create Datasets
    train_non_covid = data.Dataset(datasets_train[0], labels_non, args_dict.train_folder,
                                   transform=train_transformation)
    train_covid = data.Dataset(datasets_train[1], labels_cov, args_dict.train_folder, transform=train_transformation)

    covid_size = max(int(args_dict.batch * args_dict.covid_percent), 1)

    # create data loader
    dl_non_covid = DataLoader(train_non_covid, batch_size=(args_dict.batch - covid_size),
                              shuffle=True)  # num_workers= 2
    dl_covid = DataLoader(train_covid, batch_size=covid_size, shuffle=True)  # num_workers= 2

    return dl_non_covid, dl_covid

def trainEpoch(args_dict, dl_non_covid, dl_covid, model, criterion, optimizer, epoch):
    # object to store & plot the losses
    losses = utils.AverageMeter()
    accuracies = utils.AverageMeter()

    # switch to train mode
    model.train()
    for batch_idx, (x_batch_nc, y_batch_nc, _) in enumerate(dl_non_covid):
        x_batch_c, y_batch_c, _ = next(iter(dl_covid))

        x_batch = torch.cat((x_batch_nc, x_batch_c)).to(args_dict.device)
        y_batch = torch.cat((y_batch_nc, y_batch_c)).to(args_dict.device)
        # weights = torch.cat((weights_nc, weights_c)).to(args_dict.device)

        # Model output
        output = model(x_batch)

        # Loss
        train_loss = criterion(output, y_batch)
        losses.update(train_loss.data.cpu().numpy(), x_batch[0].size(0))

        # Accuracy
        max_indices = torch.max(output, axis=1)[1]
        train_acc = (max_indices == y_batch).sum().item() / max_indices.size()[0]
        accuracies.update(train_acc, x_batch[0].size(0))

        optimizer.zero_grad()
        train_loss.backward()
        optimizer.step()

        # Print info
        print('Train Epoch: {} [{}/{} ({:.0f}%)]\t'
              'Loss {loss.val:.4f} ({loss.avg:.4f})\t'
              'Accuracy {accuracy.val:.4f} ({accuracy.avg:.4f})\t'.format(
               epoch, batch_idx, len(dl_non_covid), 100. * batch_idx / len(dl_non_covid),
               loss=losses, accuracy=accuracies))

    # Plot loss
    plotter.plot('loss', 'train', 'Cross Entropy Loss', epoch, losses.avg)
    plotter.plot('Acc', 'train', 'Accuracy', epoch, accuracies.avg)

def train_model(args_dict):

    # Define model
    if args_dict.model == "covidnet":
        model = CovidNet(args_dict.n_classes)
    elif args_dict.model == "resnet":
        model = ResNet(args_dict.n_classes)

    print("model selected: {}".format(args_dict.model))

    model.to(args_dict.device)

    # Loss and optimizer
    optimizer = torch.optim.Adam(model.parameters(), lr=args_dict.lr)
    criterion = nn.CrossEntropyLoss(weight=torch.Tensor(args_dict.class_weights).to(args_dict.device))

    # Resume training if needed
    best_sensit, model, optimizer = utils.resume(args_dict, model, optimizer)
    scheduler = ReduceLROnPlateau(optimizer, factor=args_dict.factor, patience=args_dict.patience, verbose=True)

    # Load data
    dl_non_covid, dl_covid = calculateDataLoaderTrain(args_dict)

    # Data loading for test
    dl_test = eval.calculateDataLoaderTest(args_dict)

    # Now, let's start the training process!
    print('Start training...')
    pat_track = 0
    for epoch in range(args_dict.epochs):

        # Compute a training epoch
        trainEpoch(args_dict, dl_non_covid, dl_covid, model, criterion, optimizer, epoch)

        # Compute a validation epoch
        sensitivity_covid, accuracy = eval.valEpoch(args_dict, dl_test, model)

        scheduler.step(accuracy)

        # save if it is the best model
        if accuracy >= 0.80:  # only compare sensitivity if we have a minimum accuracy of 0.8
            is_best = sensitivity_covid > best_sensit
            if is_best:
                print("BEST MODEL FOUND!")
                best_sensit = max(sensitivity_covid, best_sensit)
                utils.save_model(args_dict, {
                    'epoch': epoch + 1,
                    'state_dict': model.state_dict(),
                    'best_sensit': best_sensit,
                    'optimizer': optimizer.state_dict(),
                    'valtrack': pat_track,
                    # 'freeVision': args_dict.freeVision,
                    'curr_val': accuracy,
                })
        print('** Validation: %f (best_sensitivity) - %f (current acc) - %d (patience)' % (best_sensit, accuracy,
                                                                                           pat_track))

        # Plot
        plotter.plot('Sensitivity', 'test', 'sensitivity covid', epoch, sensitivity_covid)
        plotter.plot('Accuracy', 'test', 'Accuracy', epoch, accuracy)

def run_train(args_dict):
    # Set seed for reproducibility
    torch.manual_seed(args_dict.seed)

    # Set up device
    if torch.cuda.is_available():
        args_dict.device = torch.device("cuda:0")  # you can continue going on here, like cuda:1 cuda:2....etc.
        print("Running on the GPU")
    else:
        args_dict.device = torch.device("cpu")
        print("Running on the CPU")

    # Plots
    global plotter
    plotter = utils.VisdomLinePlotter(env_name=args_dict.name)

    # Main process
    train_model(args_dict)
