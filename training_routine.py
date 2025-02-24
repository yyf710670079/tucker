from typing import List, Tuple
import torch
import data_loader
from data_loader import DataLoader
import numpy as np
from models import tucker
from tqdm import tqdm

device = torch.device('cuda' if torch.cuda.is_available() else 'cpu')


def unzip(x):
    x = zip(*x)
    return list(x)


def generate_negative_objects_for_triple(
        dl: DataLoader,
        s_idx: int,
        r_idx: int,
        o_idx: int) -> torch.LongTensor:
    '''
    Given a fact (s, r, o), return all the objects o' such that the triple (s,
    r, o') is not in the training set
    '''
    negative = [dl.entity_to_idx[e] for e in dl.entities if dl.entity_to_idx[e] not in dl.sr_pairs[(s_idx, r_idx)]]
    return torch.LongTensor(negative)


def generate_negative_objects(
        dl: DataLoader,
        s_idxs: torch.Tensor,
        r_idxs: torch.Tensor,
        o_idxs: torch.Tensor) -> List[torch.LongTensor]:
    '''
    Given a tensor of facts (s, r, o), for each fact return all the objects o'
    such that (s, r, o') is not present in the training dataset (i.e. negative
    facts)
    '''
    result = []

    for i in range(len(s_idxs)):
        s, r, o = s_idxs[i].item(), r_idxs[i].item(), o_idxs[i].item()

        result.append(generate_negative_objects_for_triple(dl, s, r, o))

    return result


def measure_performance(
        model: tucker.TuckER,
        dl: DataLoader,
        ks: List[int] = [1, 3, 10]) -> Tuple[int, dict]:
    '''
    Measure the performance of a model by computing a mean reciprocal rank and
    hits@k for each k in `ks`
    '''
    model.eval()
    mrr = 0
    test_facts = dl.get_all_facts('test')
    hits_k = {k: 0 for k in ks}
    batch_test_loader = torch.utils.data.DataLoader(test_facts, batch_size=100)

    for s, r, o in tqdm(batch_test_loader, 'Measuring performance'):
        output = model(s, r)
        negatives = generate_negative_objects(dl, s, r, o)

        # This shouldn't be done iteratively in the ideal world
        for i, negative in enumerate(negatives):
            rank = (output[i][negative] >= output[i][o[i]]).sum().item() + 1
            mrr += 1/rank

            for k in hits_k.keys():
                if rank <= k:
                    hits_k[k] += 1

    # normalise
    mrr /= len(test_facts)

    for k in hits_k.keys():
        hits_k[k] /= len(test_facts)

    model.train()
    return mrr, hits_k


def _train_step(
        model: tucker.TuckER, 
        data_loader, 
        batch_loader, 
        optimizer, 
        label_smoothing_rate: float, 
        desc: str=None):
    loss = torch.nn.BCELoss()
    loss_avg = 0
    for subject_index, relation_index in tqdm(batch_loader, desc=desc):
        optimizer.zero_grad()
        output = model(
            subject_index=subject_index,
            relation_index=relation_index
        )
        target = data_loader.get_y(
            subject_idxs=subject_index,
            relation_idxs=relation_index
        ).to(device)
        target = (1.0 - label_smoothing_rate) * target + label_smoothing_rate * (1.0 / target.size(1))
        loss_val = loss(output, target=target)
        loss_val.backward()
        loss_avg += loss_val.item()
        optimizer.step()
    print('Loss Val:', loss_avg)


def test(model, data_loader, batch_loader):
    model.eval()
    total_predictions, correct_predictions = 0, 0
    for subject_index, relation_index in tqdm(batch_loader, 'Testing'):
        output = model(
            subject_index=subject_index, 
            relation_index=relation_index
        )
        output = torch.round(output)
        target = data_loader.get_y(
            subject_idxs=subject_index,
            relation_idxs=relation_index
        ).to(device)
        _correct_predictions = (output == target).count_nonzero()
        _correct_predictions = int(_correct_predictions)
        correct_predictions += _correct_predictions
        total_predictions += len(torch.reshape(output, [-1]))
    model.train()
    return correct_predictions / total_predictions


def train(
        model: tucker.TuckER, 
        data_loader, 
        epochs: int, 
        lr: float, 
        lr_decay: float, 
        batch_size: int, 
        label_smoothing_rate: float,
        weight_decay: float):
    optimizer = torch.optim.Adam(
        params=model.parameters(),
        lr=lr,
        weight_decay=weight_decay
    )
    lr_scheduler = torch.optim.lr_scheduler.ExponentialLR(
        optimizer=optimizer,
        gamma=lr_decay
    )
    sl = data_loader.get_1_to_n_train_data()[0]
    batch_loader = torch.utils.data.DataLoader(list(sl.keys()), batch_size=batch_size)
    for epoch in range(epochs):
        _train_step(
            model=model,
            data_loader=data_loader,
            batch_loader=batch_loader,
            optimizer=optimizer,
            label_smoothing_rate=label_smoothing_rate,
            desc='Epoch {}'.format(epoch)
        )
        lr_scheduler.step()
        if (epoch + 1) % 10 == 0:
            print(measure_performance(model, data_loader))


if __name__ == '__main__':
    dl = data_loader.DataLoader('data/FB15k')
    model = tucker.TuckER(
        len(dl.entities),
        len(dl.relations),
        np.random.normal(size=[200, 30, 200])
    )

    train(
        model,
        data_loader=dl,
        epochs=2,
        lr=0.0001,
        lr_decay=0.99,
        batch_size=4,
        label_smoothing_rate=0.1,
        weight_decay=0
    )
